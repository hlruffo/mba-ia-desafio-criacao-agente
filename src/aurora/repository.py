from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class Repository:
    """SQLite persistence for sessions, events and condominium records."""

    def __init__(self, database: Path, seed_dir: Path) -> None:
        self.database = Path(database)
        self.seed_dir = Path(seed_dir)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS api_sessions (
                    id TEXT PRIMARY KEY, apartment TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS api_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES api_sessions(id),
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reservations (
                    code TEXT PRIMARY KEY, apartment TEXT NOT NULL, area TEXT NOT NULL,
                    date TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_reservation
                    ON reservations(area, date) WHERE active = 1;
                CREATE TABLE IF NOT EXISTS visitors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, apartment TEXT NOT NULL,
                    name TEXT NOT NULL, date TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS confirmations (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES api_sessions(id),
                    action TEXT NOT NULL, details TEXT NOT NULL, status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reservation_codes (
                    code TEXT PRIMARY KEY
                );
                """
            )

    def restore(self) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for table in ("api_events", "confirmations", "api_sessions", "visitors", "reservations", "reservation_codes"):
                db.execute(f"DELETE FROM {table}")
            reservations = self._seed("reservas.json")
            db.executemany(
                "INSERT INTO reservations(code, apartment, area, date) VALUES (?, ?, ?, ?)",
                [(r["codigo"], r["apartamento"], r["area"], r["data"]) for r in reservations],
            )
            db.executemany(
                "INSERT INTO reservation_codes(code) VALUES (?)",
                [(r["codigo"],) for r in reservations],
            )
            visitors = self._seed("visitantes.json")
            db.executemany(
                "INSERT INTO visitors(apartment, name, date) VALUES (?, ?, ?)",
                [(v["apartamento"], v["nome"], v["data"]) for v in visitors],
            )
            db.commit()

    def restore_if_empty(self) -> None:
        with self.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]
            if count:
                return
        self.restore()

    def _seed(self, filename: str) -> list[dict[str, Any]]:
        return json.loads((self.seed_dir / filename).read_text(encoding="utf-8"))

    def apartments(self) -> list[dict[str, str]]:
        return self._seed("apartamentos.json")

    def areas(self) -> list[dict[str, Any]]:
        return self._seed("areas.json")

    def log(self, session_id: str, payload: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO api_events(session_id, payload) VALUES (?, ?)",
                (session_id, json.dumps(payload, ensure_ascii=False)),
            )

    def session(self, session_id: str) -> dict[str, str] | None:
        with self.connect() as db:
            row = db.execute("SELECT id, apartment FROM api_sessions WHERE id = ?", (session_id,)).fetchone()
            return dict(row) if row else None

    def events(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM api_events WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
            return [json.loads(row["payload"]) for row in rows]

    def reservations(self, apartment: str) -> list[dict[str, str]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT code, area, date FROM reservations WHERE apartment=? AND active=1 ORDER BY rowid",
                (apartment,),
            ).fetchall()
            return [{"codigo": r["code"], "area": r["area"], "data": r["date"]} for r in rows]

    def visitors(self, apartment: str) -> list[dict[str, str]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT name, date FROM visitors WHERE apartment=? ORDER BY id", (apartment,)
            ).fetchall()
            return [{"nome": r["name"], "data": r["date"]} for r in rows]
