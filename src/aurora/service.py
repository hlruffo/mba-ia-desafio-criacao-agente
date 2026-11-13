from __future__ import annotations

import json
import re
import secrets
import sqlite3
import unicodedata
from typing import Any

from .repository import Repository


class BusinessConflict(Exception):
    """A normal business rule rejected the requested operation."""


class NotFound(Exception):
    pass


class AuroraService:
    def __init__(self, repository: Repository) -> None:
        self.repo = repository
        self._area_by_id = {area["id"]: area for area in repository.areas()}

    def create_session(self, apartment: str, session_id: str | None = None) -> str:
        session_id = session_id or secrets.token_urlsafe(18)
        with self.repo.connect() as db:
            db.execute("INSERT INTO api_sessions(id, apartment) VALUES (?, ?)", (session_id, apartment))
        return session_id

    def require_session(self, session_id: str) -> dict[str, str]:
        session = self.repo.session(session_id)
        if session is None:
            raise NotFound(session_id)
        return session

    def active_confirmations(self, session_id: str) -> list[dict[str, Any]]:
        self.require_session(session_id)
        with self.repo.connect() as db:
            rows = db.execute(
                "SELECT id, action, details FROM confirmations WHERE session_id=? AND status='pending' ORDER BY rowid",
                (session_id,),
            ).fetchall()
            return [{"id": r["id"], "acao": r["action"], "detalhes": json.loads(r["details"])} for r in rows]

    def find_reservation(self, session_id: str, area: str, date: str) -> dict[str, bool]:
        self.require_session(session_id)
        area = self._canonical_area(area)
        with self.repo.connect() as db:
            row = db.execute(
                "SELECT 1 FROM reservations WHERE area=? AND date=? AND active=1", (area, date)
            ).fetchone()
            return {"occupied": row is not None}

    def request_reservation(self, session_id: str, area: str, date: str) -> dict[str, Any]:
        session = self.require_session(session_id)
        area = self._canonical_area(area)
        if self.find_reservation(session_id, area, date)["occupied"]:
            return {"status": "occupied"}
        if self._area_by_id[area]["taxa"] > 0:
            confirmation_id = secrets.token_urlsafe(18)
            details = {"area": area, "data": date, "taxa": self._area_by_id[area]["taxa"]}
            with self.repo.connect() as db:
                db.execute(
                    "INSERT INTO confirmations(id, session_id, action, details, status) VALUES (?, ?, ?, ?, 'pending')",
                    (confirmation_id, session_id, "reservar", json.dumps(details, ensure_ascii=False)),
                )
            return {"status": "confirmation_required", "id": confirmation_id, "details": details}
        try:
            return {"status": "created", **self._insert_reservation(session["apartment"], area, date)}
        except BusinessConflict:
            return {"status": "occupied"}

    def request_cancellation(self, session_id: str, area: str, date: str) -> dict[str, str]:
        session = self.require_session(session_id)
        area = self._canonical_area(area)
        with self.repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT code FROM reservations WHERE apartment=? AND area=? AND date=? AND active=1",
                (session["apartment"], area, date),
            ).fetchone()
            if row is None:
                db.rollback()
                return {"status": "not_found"}
            db.execute("UPDATE reservations SET active=0 WHERE code=?", (row["code"],))
            db.commit()
            return {"status": "cancelled"}

    def request_visitor(self, session_id: str, name: str, date: str) -> dict[str, Any]:
        self.require_session(session_id)
        confirmation_id = secrets.token_urlsafe(18)
        details = {"nome": name.strip(), "data": date}
        with self.repo.connect() as db:
            db.execute(
                "INSERT INTO confirmations(id, session_id, action, details, status) VALUES (?, ?, ?, ?, 'pending')",
                (confirmation_id, session_id, "autorizar_visitante", json.dumps(details, ensure_ascii=False)),
            )
        return {"status": "confirmation_required", "id": confirmation_id, "details": details}

    def confirm(self, session_id: str, confirmation_id: str, confirmed: bool) -> dict[str, Any]:
        session = self.require_session(session_id)
        with self.repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            pending = db.execute(
                "SELECT action, details FROM confirmations WHERE id=? AND session_id=? AND status='pending'",
                (confirmation_id, session_id),
            ).fetchone()
            if pending is None:
                db.rollback()
                raise BusinessConflict("Confirmação inexistente ou já respondida")
            details = json.loads(pending["details"])
            action = pending["action"]
            db.execute(
                "UPDATE confirmations SET status=? WHERE id=?",
                ("approved" if confirmed else "denied", confirmation_id),
            )
            if not confirmed:
                db.commit()
                return {"status": "denied"}
            if action == "autorizar_visitante":
                db.execute(
                    "INSERT INTO visitors(apartment, name, date) VALUES (?, ?, ?)",
                    (session["apartment"], details["nome"], details["data"]),
                )
                db.commit()
                return {"status": "created", "action": action}
            try:
                result = self._insert_reservation(session["apartment"], details["area"], details["data"], db=db)
            except BusinessConflict:
                db.execute("UPDATE confirmations SET status='rejected' WHERE id=?", (confirmation_id,))
                db.commit()
                return {"status": "occupied", "action": action}
            db.commit()
            return {"status": "created", "action": action, "codigo": result["codigo"]}

    def reservations(self, apartment: str) -> list[dict[str, str]]:
        return self.repo.reservations(apartment)

    def visitors(self, apartment: str) -> list[dict[str, str]]:
        return self.repo.visitors(apartment)

    def create_visitor(self, session_id: str, name: str, date: str) -> dict[str, Any]:
        return self.request_visitor(session_id, name, date)

    def regulation_answer(self, question: str) -> str:
        """Search the regulation at call time and return only the matching provision."""
        source = (self.repo.seed_dir / "regulamento.md").read_text(encoding="utf-8")
        terms = [term for term in re.findall(r"[\wÀ-ÿ]+", question.lower()) if len(term) > 3]
        articles = list(re.finditer(r"(?m)^\*\*Art\.\s+\d+\.\*\*", source))
        candidates: list[tuple[int, str]] = []
        for index, match in enumerate(articles):
            end = articles[index + 1].start() if index + 1 < len(articles) else len(source)
            article = source[match.start() : end].strip()
            score = sum(1 for term in terms if term in article.lower())
            if score:
                candidates.append((score, article))
        if not candidates:
            return "Não encontrei um trecho específico do regulamento para essa pergunta."
        highest_score = max(score for score, _ in candidates)
        selected = [article for score, article in candidates if score == highest_score]
        return "\n\n".join(selected)[:1800]

    def _insert_reservation(
        self, apartment: str, area: str, date: str, db: sqlite3.Connection | None = None
    ) -> dict[str, str]:
        own_connection = db is None
        connection = db or self.repo.connect()
        try:
            if own_connection:
                connection.execute("BEGIN IMMEDIATE")
            code = "RSV-" + secrets.token_hex(6).upper()
            connection.execute("INSERT INTO reservation_codes(code) VALUES (?)", (code,))
            connection.execute(
                "INSERT INTO reservations(code, apartment, area, date) VALUES (?, ?, ?, ?)",
                (code, apartment, area, date),
            )
            if own_connection:
                connection.commit()
            return {"codigo": code, "area": area, "data": date}
        except sqlite3.IntegrityError as exc:
            if own_connection:
                connection.rollback()
            raise BusinessConflict("A área já está reservada nessa data") from exc
        finally:
            if own_connection:
                connection.close()

    def _canonical_area(self, area: str) -> str:
        normalized = " ".join(
            unicodedata.normalize("NFKD", area.strip().lower())
            .encode("ascii", "ignore")
            .decode("ascii")
            .split()
        )
        aliases = {
            "salao de festas": "salao-de-festas",
            "salao-de-festas": "salao-de-festas",
            "churrasqueira": "churrasqueira",
            "quadra": "quadra",
            "quadra poliesportiva": "quadra",
        }
        canonical = aliases.get(normalized)
        if canonical is None or canonical not in self._area_by_id:
            raise BusinessConflict("Área desconhecida")
        return canonical
