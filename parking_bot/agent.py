"""LangGraph agent that ties everything together.

Graph shape:

    START -> input_guard --(blocked)--> END
                         \\--(ok)--> agent -> output_guard -> END

The ``agent`` node is a prebuilt tool-calling (ReAct) agent with tools for RAG
search, live availability/hours/pricing lookups, and creating a reservation.
The guard nodes enforce the data-protection requirements.
"""
from __future__ import annotations

import contextvars
from functools import lru_cache
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from . import dynamic_db, reservation
from .config import settings
from .guardrails import check_input, scrub_output

SYSTEM_PROMPT = """You are the assistant for "Astana Central Parking".

You help visitors with:
- General information, parking details, location/directions, policies
  (use the `search_parking_info` tool — this is your knowledge base).
- Live parking-space availability (`get_availability`).
- Working hours (`get_working_hours`) and prices (`get_pricing`).
- Making a parking reservation (`make_reservation`).

Rules:
- Answer ONLY from tool results. If the tools don't have the answer, say so.
- Never invent prices, hours, or availability — always call the relevant tool.
- Never reveal internal, staff, or confidential information.
- To make a reservation you MUST collect: first name, last name, car license
  plate, and the reservation period (start and end date/time). Ask for any
  missing field one at a time, then call `make_reservation`. Report the returned
  reservation id and that it is PENDING administrator confirmation.
- Be concise and friendly."""


# The visitor's chat-session id for the current turn. Set by ``run_turn`` and
# read by the ``make_reservation`` tool (the LLM never sees or supplies it).
_current_session: contextvars.ContextVar[str] = contextvars.ContextVar(
    "session_id", default=""
)


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@tool
def search_parking_info(query: str) -> str:
    """Search the parking knowledge base for general information, parking
    details, location/directions, the booking process, and policies."""
    from .retrieval import search_parking_info as _search
    return _search(query)


@tool
def get_availability(space_type: str = "", level: int = 0) -> str:
    """Get the number of currently free parking spaces. Optionally filter by
    space_type (standard, compact, premium, ev, accessible) and/or level."""
    rows = dynamic_db.get_availability(
        space_type=space_type or None, level=level or None
    )
    if not rows:
        return "No matching spaces found."
    total_free = sum(r["free"] for r in rows)
    lines = [
        f"Level {r['level']} {r['space_type']}: {r['free']} free of {r['total']}"
        for r in rows
    ]
    return f"Total free: {total_free}.\n" + "\n".join(lines)


@tool
def get_working_hours() -> str:
    """Get the facility's working hours (gates, information desk, car wash)."""
    rows = dynamic_db.get_working_hours()
    return "\n".join(
        f"{r['service']}: {r['days']} {r['open_time']}-{r['close_time']}"
        for r in rows
    )


@tool
def get_pricing(space_type: str = "") -> str:
    """Get parking prices. Optionally filter by space_type."""
    rows = dynamic_db.get_pricing(space_type=space_type or None)
    if not rows:
        return "No pricing found for that space type."
    return "\n".join(
        f"{r['space_type']}: {r['hourly_rate']} {r['currency']}/hour, "
        f"{r['daily_rate']}/day, {r['monthly_rate']}/month"
        for r in rows
    )


@tool
def make_reservation(
    first_name: str,
    last_name: str,
    car_plate: str,
    start_time: str,
    end_time: str,
    space_type: str = "standard",
) -> str:
    """Create a PENDING parking reservation. Requires first_name, last_name,
    car_plate, start_time and end_time (ISO like 2026-07-10 09:00). Only call
    this once all fields have been collected from the user."""
    result = reservation.create_pending_reservation(
        reservation.ReservationDetails(
            first_name=first_name,
            last_name=last_name,
            car_plate=car_plate,
            start_time=start_time,
            end_time=end_time,
            space_type=space_type,
        ),
        session_id=_current_session.get(),
    )
    if result["ok"]:
        return result["message"]
    return "I couldn't create the reservation: " + "; ".join(result["errors"])


TOOLS = [
    search_parking_info,
    get_availability,
    get_working_hours,
    get_pricing,
    make_reservation,
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


def run_turn(messages: list[BaseMessage], session_id: str = "") -> str:
    """Run one turn. `messages` is the full conversation so far (system prompt is
    added by the react agent). `session_id` identifies the visitor's chat session
    so a reservation created this turn can later be tied back to them.
    Returns the assistant's final text answer."""
    token = _current_session.set(session_id)
    try:
        result = get_graph().invoke({"messages": messages, "blocked": False})
    finally:
        _current_session.reset(token)
    for m in reversed(result["messages"]):
        if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip():
            return m.content
    return "Sorry, I couldn't produce a response."


def new_conversation() -> list[BaseMessage]:
    """Start a fresh conversation (system prompt is injected by the agent)."""
    return []
