"""Tests for the FastAPI admin server.

The LLM-backed ``handle_admin_decision`` is stubbed with a fake that applies the
status change directly, so the HTTP layer is tested without any model calls.
"""
import pytest
from fastapi.testclient import TestClient

from parking_bot import admin_server, dynamic_db


@pytest.fixture()
def client(default_db, monkeypatch):
    def _fake_decision(reservation_id, decision, note=""):
        status = (
            "confirmed"
            if decision.lower().startswith(("approve", "confirm"))
            else "cancelled"
        )
        dynamic_db.set_reservation_status(int(reservation_id), status)
        return f"Reservation {reservation_id} -> {status}"

    monkeypatch.setattr(
        admin_server.admin_agent, "handle_admin_decision", _fake_decision
    )
    return TestClient(admin_server.app)


def _seed_pending(db_path):
    return dynamic_db.insert_reservation(
        "Aya", "Rai", "342GHB01",
        "2026-07-03 00:00", "2026-07-20 00:00", "standard",
        session_id="sess-web", db_path=db_path,
    )


def test_pending_endpoint_lists_reservations(client, default_db):
    rid = _seed_pending(default_db)
    resp = client.get("/admin/pending")
    assert resp.status_code == 200
    assert any(r["id"] == rid for r in resp.json())


def test_admin_home_renders_reservation_and_buttons(client, default_db):
    _seed_pending(default_db)
    resp = client.get("/admin/")
    assert resp.status_code == 200
    assert "342GHB01" in resp.text
    assert "Approve" in resp.text and "Reject" in resp.text


def test_json_decision_confirms(client, default_db):
    rid = _seed_pending(default_db)
    resp = client.post(
        "/admin/api/decision", json={"reservation_id": rid, "decision": "approve"}
    )
    assert resp.status_code == 200
    assert "confirmed" in resp.json()["message"].lower()
    assert dynamic_db.get_reservation(rid, db_path=default_db)["status"] == "confirmed"


def test_form_decision_redirects_and_cancels(client, default_db):
    rid = _seed_pending(default_db)
    resp = client.post(
        "/admin/decision",
        data={"reservation_id": rid, "decision": "reject"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/"
    assert dynamic_db.get_reservation(rid, db_path=default_db)["status"] == "cancelled"
