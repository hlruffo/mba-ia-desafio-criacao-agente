"""Domain tools shared by the ADK agents and the deterministic smoke adapter."""

from __future__ import annotations

from typing import Any

from .service import AuroraService


def _log(service: AuroraService, session_id: str, name: str, result: Any) -> Any:
    service.repo.log(session_id, {"type": "tool", "name": name, "result": result})
    return result


def reserve_area(service: AuroraService, session_id: str, area: str, date: str) -> dict[str, Any]:
    return _log(service, session_id, "reserve_area", service.request_reservation(session_id, area, date))


def cancel_reservation(service: AuroraService, session_id: str, area: str, date: str) -> dict[str, Any]:
    return _log(service, session_id, "cancel_reservation", service.request_cancellation(session_id, area, date))


def list_reservations(service: AuroraService, session_id: str) -> list[dict[str, str]]:
    apartment = service.require_session(session_id)["apartment"]
    return _log(service, session_id, "list_my_reservations", service.reservations(apartment))


def check_area_date(
    service: AuroraService, session_id: str, area: str, date: str
) -> dict[str, bool]:
    return _log(service, session_id, "check_area_date", service.find_reservation(session_id, area, date))


def authorize_visitor(
    service: AuroraService, session_id: str, name: str, date: str
) -> dict[str, Any]:
    return _log(service, session_id, "authorize_visitor", service.request_visitor(session_id, name, date))


def confirm_action(
    service: AuroraService, session_id: str, confirmation_id: str, confirmed: bool
) -> dict[str, Any]:
    return _log(
        service,
        session_id,
        "confirm_action",
        service.confirm(session_id, confirmation_id, confirmed),
    )


def list_visitors(service: AuroraService, session_id: str) -> list[dict[str, str]]:
    apartment = service.require_session(session_id)["apartment"]
    return _log(service, session_id, "list_my_visitors", service.visitors(apartment))


def search_regulation(service: AuroraService, session_id: str, question: str) -> str:
    return _log(service, session_id, "search_regulation", service.regulation_answer(question))
