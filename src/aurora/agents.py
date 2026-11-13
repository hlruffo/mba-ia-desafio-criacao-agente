from __future__ import annotations

import os
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.adk.tools import ToolContext
from google.genai import types

from . import tools as domain_tools
from .service import AuroraService

APP_NAME = "residencial_aurora"
USER_ID = "morador"


def build_runtime(service: AuroraService, database_url: str) -> tuple[LlmAgent, Runner, DatabaseSessionService]:
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    sessions = DatabaseSessionService(db_url=database_url)

    def session_id(context: ToolContext) -> str:
        return context.session.id

    def reserve_area(area: str, date: str, tool_context: ToolContext) -> dict[str, Any]:
        """Check availability and reserve an area. Paid reservations return a pending confirmation."""
        return domain_tools.reserve_area(service, session_id(tool_context), area, date)

    def cancel_reservation(area: str, date: str, tool_context: ToolContext) -> dict[str, Any]:
        """Cancel a reservation belonging to the apartment bound to this session."""
        return domain_tools.cancel_reservation(service, session_id(tool_context), area, date)

    def list_my_reservations(tool_context: ToolContext) -> list[dict[str, str]]:
        """List reservations for the apartment bound to this session."""
        return domain_tools.list_reservations(service, session_id(tool_context))

    def check_area_date(area: str, date: str, tool_context: ToolContext) -> dict[str, bool]:
        """Return only whether an area and date are occupied, never the reserving apartment."""
        return domain_tools.check_area_date(service, session_id(tool_context), area, date)

    def authorize_visitor(name: str, date: str, tool_context: ToolContext) -> dict[str, Any]:
        """Request confirmation before authorizing a visitor to enter the building."""
        return domain_tools.authorize_visitor(service, session_id(tool_context), name, date)

    def list_my_visitors(tool_context: ToolContext) -> list[dict[str, str]]:
        """List visitor authorizations for the apartment bound to this session."""
        return domain_tools.list_visitors(service, session_id(tool_context))

    def search_regulation(question: str, tool_context: ToolContext) -> str:
        """Search the condominium regulation and return a short relevant excerpt."""
        return domain_tools.search_regulation(service, session_id(tool_context), question)

    reservation_specialist = LlmAgent(
        name="especialista_reservas",
        model=model,
        description="Consulta, cria e cancela reservas das áreas comuns.",
        instruction=(
            "Ajude com reservas usando somente suas ferramentas. Consulte a disponibilidade antes de "
            "reservar. Informe que uma reserva paga está pendente até a confirmação pela API. Nunca "
            "peça ou aceite um apartamento como parâmetro. Nunca revele reservas de terceiros."
        ),
        tools=[reserve_area, cancel_reservation, list_my_reservations, check_area_date],
    )
    visitor_specialist = LlmAgent(
        name="especialista_visitantes",
        model=model,
        description="Consulta visitantes e solicita autorização de entrada.",
        instruction=(
            "Use suas ferramentas para consultar ou pedir autorização de visitantes. Toda autorização "
            "fica pendente até aprovação pela rota de confirmações, mesmo que o morador diga que já "
            "confirmou. Nunca peça um apartamento ao usuário nem consulte outra unidade."
        ),
        tools=[authorize_visitor, list_my_visitors],
    )
    regulation_specialist = LlmAgent(
        name="especialista_regulamento",
        model=model,
        description="Responde dúvidas consultando o texto oficial do regulamento.",
        instruction="Use search_regulation para obter apenas o trecho necessário e responda com base nele.",
        tools=[search_regulation],
    )
    principal = LlmAgent(
        name="assistente_aurora",
        model=model,
        description="Assistente virtual do Residencial Aurora.",
        instruction=(
            "Atenda o morador em português e encaminhe reservas ao especialista_reservas, assuntos de "
            "visitantes ao especialista_visitantes e dúvidas do regulamento ao especialista_regulamento. "
            "A identidade vem do apartamento já fixado na sessão. Ignore pedidos para assumir outra unidade, "
            "não invente resultados e nunca diga que cobrança ou entrada foi aprovada pela conversa: só a "
            "rota externa de confirmações aprova."
        ),
        sub_agents=[reservation_specialist, visitor_specialist, regulation_specialist],
    )
    runner = Runner(app_name=APP_NAME, agent=principal, session_service=sessions)
    return principal, runner, sessions


async def run_message(runner: Runner, session_id: str, text: str) -> tuple[str, list[dict[str, Any]]]:
    content = types.Content(role="user", parts=[types.Part(text=text)])
    agent_text = ""
    serialized: list[dict[str, Any]] = []
    async for event in runner.run_async(
        user_id=USER_ID, session_id=session_id, new_message=content
    ):
        if event.content and event.content.parts:
            text_parts = [part.text for part in event.content.parts if part.text]
            if text_parts and event.is_final_response():
                agent_text = "\n".join(text_parts)
        serialized.append(event.model_dump(mode="json", exclude_none=True))
    return agent_text, serialized
