
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from . import dynamic_db, reservation
from .config import settings
from .guardrails import check_input, scrub_output

SYSTEM_PROMPT = """You are the administrator's assistant for Astana Central Parking. Given a reservation and the admin's decision, confirm or refuse it. Before confirming, verify a space is still free; if not, refuse and explain. Always call apply_decision, then write one short, polite message the visitor will see."""


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@tool
def get_reservation_details(reservation_id: int) -> str:
    """
    Get the details of a reservation by its id from the database. Returns a string representation of the reservation details."""
    details = dynamic_db.get_reservation(reservation_id)
    if not details:
        return f"No reservation found with id {reservation_id}."
    return (
        f"Reservation ID: {details['id']}\n"
        f"Session ID: {details['session_id']}\n"
        f"First Name: {details['first_name']}\n"
        f"Last Name: {details['last_name']}\n"
        f"Car Plate: {details['car_plate']}\n"
        f"Start Time: {details['start_time']}\n"
        f"End Time: {details['end_time']}\n"
        f"Space Type: {details['space_type']}\n"
        f"Status: {details['status']}"
    )


@tool
def check_space_availability(space_type: str = "", level: int = 0) -> str:
    """Check the number of currently free parking spaces. Optionally filter by
    space_type (standard, compact, premium, ev, accessible) and/or level."""
    rows = dynamic_db.get_availability(
        space_type=space_type or None, level=level or None
    )
    total_free = sum(r["free"] for r in rows)
    lines = [
        f"Level {r['level']} {r['space_type']}: {r['free']} free of {r['total']}"
        for r in rows
    ]
    if total_free == 0:
        return "No free spaces available."
    return f"Total free: {total_free}.\n" + "\n".join(lines)


@tool
def apply_decision(reservation_id: int, decision: str, reason: str) -> str:
    """Apply a decision to a reservation (approve or reject)."""
    d = decision.strip().lower()
    if d in ("approve", "approved", "confirm", "confirmed", "accept"):
        status, verb = "confirmed", "CONFIRMED"
    elif d in ("reject", "refuse", "refused", "cancel", "cancelled", "deny"):
        status, verb = "cancelled", "REFUSED"
    else:
        return f"Unknown decision '{decision}'. Use approve or reject."
    # Look up the reservation so we can notify the correct visitor session.
    res = dynamic_db.get_reservation(reservation_id)
    if not res:
        return f"No reservation found with id {reservation_id}."
    dynamic_db.set_reservation_status(reservation_id, status)
    dynamic_db.add_notification(
        session_id=res["session_id"],
        reservation_id=reservation_id,
        message=f"Your reservation #{reservation_id} has been {verb}."
        + (f" {reason}" if reason else ""),
    )
    return f"Decision applied to reservation {reservation_id}: {status} ({reason})"



TOOLS = [
    get_reservation_details,
    check_space_availability,
    apply_decision,
]


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #
class BotState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    blocked: bool


@lru_cache(maxsize=1)
def _react_agent():
    settings.require_openai()
    from langchain_openai import ChatOpenAI
    from langgraph.prebuilt import create_react_agent

    llm = ChatOpenAI(
        model=settings.chat_model, temperature=0, api_key=settings.openai_api_key
    )
    return create_react_agent(llm, TOOLS, prompt=SYSTEM_PROMPT)


def _input_guard_node(state: BotState) -> dict:
    last_human = next(
        (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None
    )
    if last_human is not None:
        verdict = check_input(str(last_human.content))
        if not verdict.allowed:
            return {"messages": [AIMessage(content=verdict.reason)], "blocked": True}
    return {"blocked": False}


def _agent_node(state: BotState) -> dict:
    result = _react_agent().invoke({"messages": state["messages"]})
    # Return only the new messages produced by the sub-agent.
    new = result["messages"][len(state["messages"]):]
    return {"messages": new}


def _output_guard_node(state: BotState) -> dict:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and isinstance(last.content, str):
        cleaned = scrub_output(last.content)
        if cleaned != last.content:
            return {"messages": [AIMessage(content=cleaned, id=last.id)]}
    return {}


def _route_after_input(state: BotState) -> str:
    return END if state.get("blocked") else "agent"


@lru_cache(maxsize=1)
def build_graph():
    g = StateGraph(BotState)
    g.add_node("input_guard", _input_guard_node)
    g.add_node("agent", _agent_node)
    g.add_node("output_guard", _output_guard_node)

    g.add_edge(START, "input_guard")
    g.add_conditional_edges("input_guard", _route_after_input, ["agent", END])
    g.add_edge("agent", "output_guard")
    g.add_edge("output_guard", END)
    return g.compile()


# Exposed for `langgraph dev` if desired.
graph = None


def get_graph():
    global graph
    if graph is None:
        graph = build_graph()
    return graph


def run_turn(messages: list[BaseMessage]) -> str:
    """Run one turn. `messages` is the full conversation so far (system prompt is
    added by the react agent). Returns the assistant's final text answer."""
    result = get_graph().invoke({"messages": messages, "blocked": False})
    for m in reversed(result["messages"]):
        if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip():
            return m.content
    return "Sorry, I couldn't produce a response."


def new_conversation() -> list[BaseMessage]:
    """Start a fresh conversation (system prompt is injected by the agent)."""
    return []


def handle_admin_decision(reservation_id: int, decision: str, note: str = "") -> str:
    """Entry point for the admin REST server: run the admin agent on a single
    decision. The agent verifies availability, updates the reservation status,
    and queues a notification for the visitor. Returns the agent's message."""
    msg = HumanMessage(
        content=(
            f"Reservation {reservation_id}. Admin decision: {decision}. "
            f"Note: {note or 'none'}. Apply it now."
        )
    )
    return run_turn([msg])
