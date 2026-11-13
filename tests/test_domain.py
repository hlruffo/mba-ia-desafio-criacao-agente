from concurrent.futures import ThreadPoolExecutor

from aurora.repository import Repository
from aurora.service import AuroraService, BusinessConflict


def make_service(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3", seed_dir=__import__("pathlib").Path("dados"))
    repo.restore()
    return AuroraService(repo)


def test_paid_action_requires_confirmation_and_replay_is_rejected(tmp_path):
    service = make_service(tmp_path)
    session_id = service.create_session("101")
    pending = service.request_reservation(session_id, "salao-de-festas", "2030-04-20")
    assert pending["status"] == "confirmation_required"
    assert not any(row["area"] == "salao-de-festas" and row["data"] == "2030-04-20" for row in service.reservations("101"))
    assert service.confirm(session_id, pending["id"], True)["status"] == "created"
    assert sum(row["area"] == "salao-de-festas" and row["data"] == "2030-04-20" for row in service.reservations("101")) == 1
    try:
        service.confirm(session_id, pending["id"], True)
    except BusinessConflict:
        pass
    else:
        raise AssertionError("confirmation replay must be rejected")


def test_free_reservation_and_cancellation_need_no_confirmation(tmp_path):
    service = make_service(tmp_path)
    session_id = service.create_session("101")
    result = service.request_reservation(session_id, "quadra", "2030-04-06")
    assert result["status"] == "created"
    assert service.request_cancellation(session_id, "quadra", "2030-04-06")["status"] == "cancelled"


def test_area_display_name_is_normalized_for_tools(tmp_path):
    service = make_service(tmp_path)
    session_id = service.create_session("101")
    result = service.request_cancellation(session_id, "salão de festas", "2030-03-16")
    assert result["status"] == "not_found"


def test_visitor_is_also_confirmed_and_identity_is_session_bound(tmp_path):
    service = make_service(tmp_path)
    session_id = service.create_session("101")
    pending = service.request_visitor(session_id, "Joana Ribeiro", "2030-04-21")
    assert pending["status"] == "confirmation_required"
    assert service.visitors("101") == []
    service.confirm(session_id, pending["id"], True)
    assert service.visitors("101") == [{"nome": "Joana Ribeiro", "data": "2030-04-21"}]
    assert {"nome": "Marina Duarte", "data": "2030-03-16"} in service.visitors("302")


def test_tools_use_session_apartment_and_only_return_occupancy(tmp_path):
    service = make_service(tmp_path)
    session_id = service.create_session("101")
    assert service.find_reservation(session_id, "salao-de-festas", "2030-03-16")["occupied"] is True
    assert service.find_reservation(session_id, "salao-de-festas", "2030-03-16") == {"occupied": True}
    assert all(item["codigo"] != "RSV-4821" for item in service.reservations("101"))


def test_regulation_lookup_returns_the_relevant_article_only(tmp_path):
    service = make_service(tmp_path)
    answer = service.regulation_answer("Até que horas a piscina funciona aos domingos?")
    assert "Aos domingos e feriados, a piscina funciona das 9h às 20h" in answer
    assert "**Art. 21.**" not in answer
    assert "**Art. 23.**" not in answer


def test_two_concurrent_paid_approvals_have_one_winner(tmp_path):
    service = make_service(tmp_path)
    first = service.create_session("101")
    second = service.create_session("201")
    p1 = service.request_reservation(first, "salao-de-festas", "2030-05-11")
    p2 = service.request_reservation(second, "salao-de-festas", "2030-05-11")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.confirm, sid, p["id"], True) for sid, p in ((first, p1), (second, p2))]
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except BusinessConflict:
                results.append({"status": "occupied"})
    assert sorted(item["status"] for item in results) == ["created", "occupied"]
    rows = service.reservations("101") + service.reservations("201")
    assert sum(row["area"] == "salao-de-festas" and row["data"] == "2030-05-11" for row in rows) == 1
