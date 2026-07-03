"""Integration tests for the Stage-4 LangGraph orchestration pipeline.

Drives the whole graph end-to-end (user_interaction → admin_approval[interrupt]
→ record / finalize) with the MCP path disabled so writes hit the temp store.
No LLM is required — the pipeline logic is deterministic.
"""
from parking_bot import dynamic_db, orchestrator, reservation_store

BOOKING = {
    "first_name": "Aya",
    "last_name": "Rai",
    "car_plate": "342GHB01",
    "start_time": "2026-07-10 09:00",
    "end_time": "2026-07-10 18:00",
    "space_type": "standard",
}


def test_graph_has_the_three_required_nodes():
    g = orchestrator.build_graph()
    nodes = set(g.get_graph().nodes)
    for required in ("user_interaction", "admin_approval", "record"):
        assert required in nodes


def test_intake_pauses_at_admin_approval(pipeline_env):
    g = orchestrator.build_graph()
    started = orchestrator.start_pipeline(BOOKING, session_id="s-ok", graph=g)
    assert started["reservation_id"] is not None
    assert started["awaiting_admin"] is True
    assert started["interrupt"]["type"] == "admin_approval"
    assert started["interrupt"]["reservation_id"] == started["reservation_id"]
    # Still pending until the administrator decides.
    assert dynamic_db.get_reservation(
        started["reservation_id"], db_path=pipeline_env["db"]
    )["status"] == "pending"


def test_approve_confirms_records_and_notifies(pipeline_env):
    g = orchestrator.build_graph()
    started = orchestrator.start_pipeline(BOOKING, session_id="s-ok", graph=g)
    done = orchestrator.resume_pipeline(
        started["thread_id"], "approve", reason="Looks good.", graph=g
    )

    assert done["status"] == "confirmed"
    # Node 3 wrote the required line format to the store.
    lines = reservation_store.read_entries(pipeline_env["store"])
    assert len(lines) == 1
    assert lines[0].startswith("Aya Rai | 342GHB01 | 2026-07-10 09:00 to 2026-07-10 18:00 |")
    # DB status + visitor notification.
    assert dynamic_db.get_reservation(
        started["reservation_id"], db_path=pipeline_env["db"]
    )["status"] == "confirmed"
    notes = dynamic_db.get_unseen_notifications("s-ok", db_path=pipeline_env["db"])
    assert notes and "CONFIRMED" in notes[0]["message"]


def test_reject_cancels_and_records_nothing(pipeline_env):
    g = orchestrator.build_graph()
    started = orchestrator.start_pipeline(BOOKING, session_id="s-no", graph=g)
    done = orchestrator.resume_pipeline(
        started["thread_id"], "reject", reason="Lot full.", graph=g
    )

    assert done["status"] == "cancelled"
    assert "REFUSED" in done["outcome"]
    assert reservation_store.read_entries(pipeline_env["store"]) == []
    notes = dynamic_db.get_unseen_notifications("s-no", db_path=pipeline_env["db"])
    assert notes and "REFUSED" in notes[0]["message"]


def test_invalid_booking_ends_before_admin(pipeline_env):
    g = orchestrator.build_graph()
    bad = {**BOOKING, "first_name": "", "car_plate": "!!"}
    started = orchestrator.start_pipeline(bad, session_id="s-bad", graph=g)
    assert started["awaiting_admin"] is False
    assert started["status"] == "error"
    assert started["reservation_id"] is None
    assert reservation_store.read_entries(pipeline_env["store"]) == []


def test_run_full_convenience_approve(pipeline_env):
    g = orchestrator.build_graph()
    done = orchestrator.run_full(BOOKING, "approve", session_id="s-full", graph=g)
    assert done["status"] == "confirmed"
    assert done["recorded"].startswith("Aya Rai | 342GHB01 |")
