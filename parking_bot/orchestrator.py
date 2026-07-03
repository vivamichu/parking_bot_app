"""Stage 4 — unified LangGraph orchestration of the whole pipeline.

A single :class:`StateGraph` ties every stage together:

    START
      │
      ▼
  user_interaction   ← Stage 1: the RAG chatbot collected the booking; create a
      │                          PENDING reservation (or end if invalid).
      ▼
  admin_approval     ← Stage 2: human-in-the-loop. The node calls ``interrupt()``
      │                          and the graph PAUSES until an administrator
      │                          supplies approve/reject (resumed via ``Command``).
      ├── confirmed ──▶ record   ← Stage 3: write the confirmed reservation to the
      │                             MCP-backed store.
      └── cancelled ──▶ finalize
                          │
                          ▼
                         END

The graph is compiled with a checkpointer so the ``admin_approval`` interrupt can
be resumed later (even, with a persistent checkpointer, from another process).

Public helpers:
    * ``start_pipeline(request, session_id)`` — run intake, pause at admin.
    * ``resume_pipeline(thread_id, decision, reason)`` — resume → record/finalize.
    * ``run_full(request, decision, ...)`` — do both in one process (demo/tests).
"""
from __future__ import annotations

import logging
import uuid
from typing import Annotated, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

from . import dynamic_db, recording, reservation

logger = logging.getLogger(__name__)


class PipelineState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
    request: dict  # reservation details collected from the user
    reservation_id: Optional[int]
    intake_message: str
    decision: str
    decision_reason: str
    status: str  # pending | confirmed | cancelled | error
    recorded: str
    outcome: str


# --------------------------------------------------------------------------- #
# Node 1 — user interaction (RAG chatbot context)
# --------------------------------------------------------------------------- #
def user_interaction_node(state: PipelineState) -> dict:
    """Persist the reservation the chatbot collected as a PENDING record.

    In the deployed system the RAG chatbot (``agent.py``) gathers first/last
    name, plate and period over a conversation and hands them here as
    ``state['request']``; this node validates and creates the pending row.
    """
    req = state.get("request") or {}
    session_id = state.get("session_id", "")
    result = reservation.create_pending_reservation(
        reservation.ReservationDetails(
            first_name=req.get("first_name", ""),
            last_name=req.get("last_name", ""),
            car_plate=req.get("car_plate", ""),
            start_time=req.get("start_time", ""),
            end_time=req.get("end_time", ""),
            space_type=req.get("space_type", "standard"),
        ),
        session_id=session_id,
    )
    if not result["ok"]:
        msg = "Could not create the reservation: " + "; ".join(result["errors"])
        return {
            "status": "error",
            "reservation_id": None,
            "outcome": msg,
            "messages": [AIMessage(content=msg)],
        }
    return {
        "status": "pending",
        "reservation_id": result["reservation_id"],
        "intake_message": result["message"],
        "messages": [AIMessage(content=result["message"])],
    }


def _route_after_intake(state: PipelineState) -> str:
    return "admin_approval" if state.get("reservation_id") else END


# --------------------------------------------------------------------------- #
# Node 2 — administrator approval (human-in-the-loop via interrupt)
# --------------------------------------------------------------------------- #
_APPROVE_WORDS = {"approve", "approved", "confirm", "confirmed", "accept", "yes"}


def admin_approval_node(state: PipelineState) -> dict:
    rid = state["reservation_id"]
    res = dynamic_db.get_reservation(rid)

    # PAUSE here until a human administrator provides a decision. The value
    # passed to Command(resume=...) is returned from interrupt().
    human = interrupt(
        {
            "type": "admin_approval",
            "reservation_id": rid,
            "reservation": res,
            "prompt": "Approve or reject this reservation.",
        }
    )

    if isinstance(human, str):
        decision_raw, reason = human, ""
    else:
        human = human or {}
        decision_raw = human.get("decision", "reject")
        reason = human.get("reason", "")

    approved = decision_raw.strip().lower() in _APPROVE_WORDS
    status, verb = ("confirmed", "CONFIRMED") if approved else ("cancelled", "REFUSED")

    dynamic_db.set_reservation_status(rid, status)
    dynamic_db.add_notification(
        session_id=res["session_id"],
        reservation_id=rid,
        message=f"Your reservation #{rid} has been {verb}."
        + (f" {reason}" if reason else ""),
    )
    return {"decision": status, "decision_reason": reason, "status": status}


def _route_after_admin(state: PipelineState) -> str:
    return "record" if state.get("status") == "confirmed" else "finalize"


# --------------------------------------------------------------------------- #
# Node 3 — data recording (MCP server)
# --------------------------------------------------------------------------- #
def record_node(state: PipelineState) -> dict:
    rid = state["reservation_id"]
    res = dynamic_db.get_reservation(rid)
    try:
        line = recording.record_confirmed(res)
        return {
            "recorded": line,
            "outcome": f"Reservation #{rid} CONFIRMED and recorded to the store.",
        }
    except Exception as exc:  # noqa: BLE001 - never crash the pipeline on recording
        logger.warning("Recording failed for reservation %s: %s", rid, exc)
        return {"outcome": f"Reservation #{rid} CONFIRMED but recording failed: {exc}"}


def finalize_node(state: PipelineState) -> dict:
    rid = state.get("reservation_id")
    return {"outcome": f"Reservation #{rid} was REFUSED by the administrator."}


# --------------------------------------------------------------------------- #
# Graph assembly
# --------------------------------------------------------------------------- #
def build_graph(checkpointer=None):
    g = StateGraph(PipelineState)
    g.add_node("user_interaction", user_interaction_node)
    g.add_node("admin_approval", admin_approval_node)
    g.add_node("record", record_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "user_interaction")
    g.add_conditional_edges("user_interaction", _route_after_intake, ["admin_approval", END])
    g.add_conditional_edges("admin_approval", _route_after_admin, ["record", "finalize"])
    g.add_edge("record", END)
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer or InMemorySaver())


_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


# --------------------------------------------------------------------------- #
# Orchestration helpers
# --------------------------------------------------------------------------- #
def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def start_pipeline(request: dict, session_id: str = "", thread_id: str | None = None, graph=None) -> dict:
    """Run intake and pause at the administrator-approval interrupt."""
    graph = graph or get_graph()
    thread_id = thread_id or uuid.uuid4().hex
    state = graph.invoke(
        {"request": request, "session_id": session_id, "messages": []},
        config=_config(thread_id),
    )
    interrupts = state.get("__interrupt__") or []
    payload = interrupts[0].value if interrupts else None
    return {
        "thread_id": thread_id,
        "reservation_id": state.get("reservation_id"),
        "intake_message": state.get("intake_message"),
        "status": state.get("status"),
        "outcome": state.get("outcome"),
        "awaiting_admin": payload is not None,
        "interrupt": payload,
    }


def resume_pipeline(thread_id: str, decision: str, reason: str = "", graph=None) -> dict:
    """Resume a paused pipeline with the administrator's decision."""
    graph = graph or get_graph()
    state = graph.invoke(
        Command(resume={"decision": decision, "reason": reason}),
        config=_config(thread_id),
    )
    return {
        "thread_id": thread_id,
        "reservation_id": state.get("reservation_id"),
        "status": state.get("status"),
        "recorded": state.get("recorded"),
        "outcome": state.get("outcome"),
    }


def run_full(request: dict, decision: str, session_id: str = "", reason: str = "", graph=None) -> dict:
    """Convenience: intake → approval → record in a single process (demo/tests)."""
    graph = graph or get_graph()
    started = start_pipeline(request, session_id=session_id, graph=graph)
    if not started["awaiting_admin"]:
        return started
    resumed = resume_pipeline(started["thread_id"], decision, reason=reason, graph=graph)
    resumed["intake_message"] = started["intake_message"]
    return resumed


# --------------------------------------------------------------------------- #
# CLI demo
# --------------------------------------------------------------------------- #
def _demo() -> None:  # pragma: no cover - manual demo helper
    logging.basicConfig(level=logging.INFO)
    dynamic_db.init_db()
    graph = build_graph()

    booking = {
        "first_name": "Aya",
        "last_name": "Rai",
        "car_plate": "342GHB01",
        "start_time": "2026-07-10 09:00",
        "end_time": "2026-07-10 18:00",
        "space_type": "standard",
    }

    print("\n=== APPROVE path ===")
    started = start_pipeline(booking, session_id="demo-approve", graph=graph)
    print("intake:", started["intake_message"])
    print("awaiting admin:", started["awaiting_admin"], "res id:", started["reservation_id"])
    done = resume_pipeline(started["thread_id"], "approve", reason="Looks good.", graph=graph)
    print("outcome:", done["outcome"])
    print("recorded line:", done["recorded"])

    print("\n=== REJECT path ===")
    started = start_pipeline({**booking, "car_plate": "999ZZZ09"}, session_id="demo-reject", graph=graph)
    done = resume_pipeline(started["thread_id"], "reject", reason="Lot full.", graph=graph)
    print("outcome:", done["outcome"])


if __name__ == "__main__":  # pragma: no cover
    _demo()
