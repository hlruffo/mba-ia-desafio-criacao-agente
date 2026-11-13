from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import tools as domain_tools
from .agents import APP_NAME, USER_ID, build_runtime, run_message
from .repository import Repository
from .service import AuroraService, BusinessConflict, NotFound

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
DATABASE = Path(os.getenv("AURORA_DATABASE", str(ROOT / "data" / "aurora.sqlite3"))).resolve()
DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE}"
repository = Repository(DATABASE, ROOT / "dados")
service = AuroraService(repository)
_, runner, adk_sessions = build_runtime(service, DATABASE_URL)


class NewSession(BaseModel):
    apartamento: str


class Message(BaseModel):
    texto: str = Field(min_length=1)


class Confirmation(BaseModel):
    id: str
    confirmado: bool


def _area(text: str) -> str | None:
    lowered = text.lower()
    for key, words in {
        "salao-de-festas": ("salão", "salao", "festas"),
        "churrasqueira": ("churrasqueira",),
        "quadra": ("quadra",),
    }.items():
        if any(word in lowered for word in words):
            return key
    return None


def _date(text: str) -> str | None:
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    return match.group(1) if match else None


def _fake_message(session_id: str, text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("visitante", "visitantes", "entrada", "libera", "autoriza")):
        if any(word in lowered for word in ("quais", "quem", "lista", "tenho")) and "libera" not in lowered:
            rows = domain_tools.list_visitors(service, session_id)
            return "Seus visitantes autorizados: " + ("; ".join(f"{r['nome']} em {r['data']}" for r in rows) or "nenhum.")
        name_match = re.search(r"(?:da|do|para|entrada da|entrada do)\s+([A-ZÀ-Ý][\wÀ-ÿ]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ]+){0,3})", text)
        date = _date(text)
        if name_match and date:
            outcome = domain_tools.authorize_visitor(service, session_id, name_match.group(1).strip(), date)
            return "A autorização de " + name_match.group(1).strip() + f" para {date} está pendente de confirmação." if outcome["status"] == "confirmation_required" else "Não foi possível autorizar."
    if any(word in lowered for word in ("regulamento", "piscina", "funciona", "horas", "horário")):
        return domain_tools.search_regulation(service, session_id, text)
    area, date = _area(text), _date(text)
    if any(word in lowered for word in ("cancele", "cancelar", "cancela")) and area and date:
        result = domain_tools.cancel_reservation(service, session_id, area, date)
        return "Reserva cancelada." if result["status"] == "cancelled" else "Não encontrei uma reserva sua nessa área e data."
    if any(word in lowered for word in ("reserve", "reservar", "reserva")) and area and date:
        result = domain_tools.reserve_area(service, session_id, area, date)
        if result["status"] == "occupied":
            return "A área já está ocupada nessa data."
        if result["status"] == "confirmation_required":
            return f"A reserva de {area} em {date} está pendente de confirmação."
        return f"Reserva de {area} em {date} criada."
    if any(word in lowered for word in ("minhas reservas", "minha reserva", "reservas agora", "quais reservas")):
        rows = domain_tools.list_reservations(service, session_id)
        return "Suas reservas: " + ("; ".join(f"{r['area']} em {r['data']}" for r in rows) or "nenhuma.")
    return "Posso ajudar com reservas, cancelamentos, visitantes e dúvidas do regulamento."


def _adk_called(events: list[dict[str, Any]], tool_name: str) -> bool:
    serialized = json.dumps(events, ensure_ascii=False)
    return f'"name": "{tool_name}"' in serialized


def _critical_action_fallback(
    session_id: str, text: str, events: list[dict[str, Any]]
) -> str | None:
    """Ensure a model's conversational shortcut cannot skip a critical tool."""
    lowered = text.lower()
    area, date = _area(text), _date(text)
    if any(word in lowered for word in ("reserve", "reservar", "reserva")) and area and date:
        if _adk_called(events, "reserve_area"):
            return None
        result = domain_tools.reserve_area(service, session_id, area, date)
        if result["status"] == "confirmation_required":
            return f"A reserva de {area} em {date} está pendente de confirmação."
        if result["status"] == "occupied":
            return "A área já está ocupada nessa data."
        return f"Reserva de {area} em {date} criada."
    if any(word in lowered for word in ("cancele", "cancelar", "cancela")) and area and date:
        if _adk_called(events, "cancel_reservation"):
            return None
        result = domain_tools.cancel_reservation(service, session_id, area, date)
        return (
            "Reserva cancelada."
            if result["status"] == "cancelled"
            else "Não encontrei uma reserva sua nessa área e data."
        )
    if any(word in lowered for word in ("visitante", "entrada", "libera", "autoriza")):
        name_match = re.search(
            r"(?:da|do|para|entrada da|entrada do)\s+([A-ZÀ-Ý][\wÀ-ÿ]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ]+){0,3})",
            text,
        )
        if name_match and date and not _adk_called(events, "authorize_visitor"):
            name = name_match.group(1).strip()
            result = domain_tools.authorize_visitor(service, session_id, name, date)
            if result["status"] == "confirmation_required":
                return f"A autorização de {name} para {date} está pendente de confirmação."
    return None


@asynccontextmanager
async def lifespan(_: FastAPI):
    repository.restore_if_empty()
    yield


app = FastAPI(title="Residencial Aurora", lifespan=lifespan)


@app.exception_handler(NotFound)
async def session_not_found(_: Any, exc: NotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Sessão não encontrada"})


def _reply(session_id: str, response: str) -> dict[str, Any]:
    return {"resposta": response, "confirmacoes_pendentes": service.active_confirmations(session_id)}


@app.post("/sessoes", status_code=201)
async def create_session(body: NewSession) -> dict[str, str]:
    session_id = service.create_session(body.apartamento)
    await adk_sessions.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
        state={"apartment": body.apartamento},
    )
    repository.log(session_id, {"type": "session_created", "apartamento": body.apartamento})
    return {"session_id": session_id}


@app.post("/sessoes/{session_id}/mensagens")
async def send_message(session_id: str, body: Message) -> dict[str, Any]:
    service.require_session(session_id)
    repository.log(session_id, {"type": "user_message", "content": body.texto})
    if os.getenv("AURORA_FAKE_MODEL", "").lower() in {"1", "true", "yes"}:
        response = _fake_message(session_id, body.texto)
    else:
        try:
            response, adk_events = await run_message(runner, session_id, body.texto)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Falha ao executar o agente. Verifique GOOGLE_API_KEY.") from exc
        for event in adk_events:
            repository.log(session_id, {"type": "adk_event", "content": event})
        fallback = _critical_action_fallback(session_id, body.texto, adk_events)
        if fallback is not None:
            response = fallback
    repository.log(session_id, {"type": "assistant_message", "content": response})
    return _reply(session_id, response)


@app.post("/sessoes/{session_id}/confirmacoes")
async def answer_confirmation(session_id: str, body: Confirmation) -> dict[str, Any]:
    service.require_session(session_id)
    try:
        result = domain_tools.confirm_action(service, session_id, body.id, body.confirmado)
    except BusinessConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result["status"] == "created":
        response = "Ação confirmada e executada."
    elif result["status"] == "occupied":
        response = "A área foi reservada por outro morador antes da confirmação."
    else:
        response = "Ação recusada; nenhuma alteração foi feita."
    return _reply(session_id, response)


@app.get("/sessoes/{session_id}/eventos")
async def get_events(session_id: str) -> list[dict[str, Any]]:
    service.require_session(session_id)
    return repository.events(session_id)


@app.get("/apartamentos/{apartamento}/reservas")
async def get_reservations(apartamento: str) -> list[dict[str, str]]:
    return service.reservations(apartamento)


@app.get("/apartamentos/{apartamento}/visitantes")
async def get_visitors(apartamento: str) -> list[dict[str, str]]:
    return service.visitors(apartamento)
