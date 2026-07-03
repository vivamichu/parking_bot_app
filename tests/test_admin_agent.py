"""Tests for the admin (second) agent.

These exercise the decision *tools* directly — no LLM call is needed, since the
DB-mutating logic lives in the tools. ``handle_admin_decision`` wiring is tested
with the react agent stubbed out.
"""
from parking_bot import admin_agent, dynamic_db, reservation_store


def _seed_pending(db_path, session_id="sess-x"):
    return dynamic_db.insert_reservation(
        "Aya", "Rai", "342GHB01",
        "2026-07-03 00:00", "2026-07-20 00:00", "standard",
        session_id=session_id, db_path=db_path,
    )


def test_apply_decision_approve_confirms_notifies_and_records(pipeline_env):
    db, store = pipeline_env["db"], pipeline_env["store"]
    rid = _seed_pending(db, session_id="sess-approve")
    out = admin_agent.apply_decision.invoke(
        {"reservation_id": rid, "decision": "approve", "reason": "Looks good."}
    )
    assert "confirmed" in out.lower()
    assert dynamic_db.get_reservation(rid, db_path=db)["status"] == "confirmed"

    notes = dynamic_db.get_unseen_notifications("sess-approve", db_path=db)
    assert len(notes) == 1
    assert "CONFIRMED" in notes[0]["message"]

    # Stage 3: the confirmed reservation is written to the file store.
    lines = reservation_store.read_entries(store)
    assert len(lines) == 1
    assert lines[0].startswith("Aya Rai | 342GHB01 | 2026-07-03 00:00 to 2026-07-20 00:00 |")


def test_apply_decision_reject_cancels_notifies_and_records_nothing(pipeline_env):
    db, store = pipeline_env["db"], pipeline_env["store"]
    rid = _seed_pending(db, session_id="sess-reject")
    out = admin_agent.apply_decision.invoke(
        {"reservation_id": rid, "decision": "reject", "reason": "Lot full that day."}
    )
    assert "cancelled" in out.lower()
    assert dynamic_db.get_reservation(rid, db_path=db)["status"] == "cancelled"

    notes = dynamic_db.get_unseen_notifications("sess-reject", db_path=db)
    assert notes and "REFUSED" in notes[0]["message"]

    # A rejection must NOT write to the confirmed-reservation store.
    assert reservation_store.read_entries(store) == []


def test_apply_decision_unknown_leaves_status_untouched(default_db):
    rid = _seed_pending(default_db)
    out = admin_agent.apply_decision.invoke(
        {"reservation_id": rid, "decision": "maybe", "reason": ""}
    )
    assert "unknown decision" in out.lower()
    assert dynamic_db.get_reservation(rid, db_path=default_db)["status"] == "pending"


def test_apply_decision_missing_reservation(default_db):
    out = admin_agent.apply_decision.invoke(
        {"reservation_id": 9999, "decision": "approve", "reason": ""}
    )
    assert "no reservation" in out.lower()


def test_get_reservation_details_tool(default_db):
    rid = _seed_pending(default_db)
    out = admin_agent.get_reservation_details.invoke({"reservation_id": rid})
    assert "342GHB01" in out
    assert str(rid) in out


def test_check_space_availability_tool(default_db):
    out = admin_agent.check_space_availability.invoke({"space_type": "standard"})
    assert "free" in out.lower()


def test_handle_admin_decision_delegates_to_agent(monkeypatch):
    captured = {}

    def _fake_run_turn(messages):
        captured["messages"] = messages
        return "decision applied"

    monkeypatch.setattr(admin_agent, "run_turn", _fake_run_turn)
    out = admin_agent.handle_admin_decision(7, "approve", "ok")
    assert out == "decision applied"
    # The reservation id and decision are threaded into the prompt.
    assert "7" in str(captured["messages"][0].content)
    assert "approve" in str(captured["messages"][0].content).lower()
