"""Streamlit chat UI for the Astana Central Parking assistant."""
from __future__ import annotations

import uuid

import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage

from parking_bot import dynamic_db
from parking_bot.agent import run_turn
from parking_bot.config import settings

st.set_page_config(page_title="Astana Central Parking", page_icon="🅿️")

# Ensure the dynamic DB exists (safe/idempotent).
dynamic_db.init_db()

# Stable id for this visitor's chat session — links reservations & notifications.
if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex
st.session_state.setdefault("pending", False)

# Auto-refresh so admin decisions surface without the user typing (optional dep).
# IMPORTANT: only mount the timer while idle. If it fired during an LLM turn it
# would restart the script and discard the in-progress answer.
if not st.session_state.pending:
    try:
        from streamlit_autorefresh import st_autorefresh

        st_autorefresh(interval=5000, key="notif_poll")
    except ModuleNotFoundError:
        pass


def _drain_notifications() -> None:
    """Pull any admin decisions queued for this session into the chat."""
    if "messages" not in st.session_state:
        return
    unseen = dynamic_db.get_unseen_notifications(st.session_state.session_id)
    if not unseen:
        return
    for n in unseen:
        st.session_state.messages.append(AIMessage(content=n["message"]))
    dynamic_db.mark_notifications_seen([n["id"] for n in unseen])


@st.cache_data(show_spinner=False)
def _facts() -> dict:
    return {
        "free": dynamic_db.get_total_free(),
        "hours": dynamic_db.get_working_hours(),
    }


with st.sidebar:
    st.header("🅿️ Astana Central Parking")
    st.caption("RAG assistant — Stage 2 (admin approval)")
    facts = _facts()
    st.metric("Free spaces right now", facts["free"])
    st.subheader("Working hours")
    for h in facts["hours"]:
        st.write(f"**{h['service']}** — {h['days']} {h['open_time']}-{h['close_time']}")
    st.divider()
    st.caption(f"Model: {settings.chat_model}")
    if st.button("🔄 Reset conversation"):
        st.session_state.pop("messages", None)
        st.rerun()
    if st.button("📨 Check for updates"):
        st.rerun()
    st.caption(
        "Try: *What are your prices?* · *How many EV spots are free?* · "
        "*I want to book a space.*"
    )

st.title("🅿️ Parking Assistant")

if "messages" not in st.session_state:
    st.session_state.messages = []  # list[BaseMessage]
    st.session_state.messages.append(
        AIMessage(
            content="Hi! I'm the Astana Central Parking assistant. Ask me about "
            "prices, availability, hours, or location — or say you'd like to book "
            "a space."
        )
    )

# Surface any admin decisions that arrived since the last rerun.
_drain_notifications()

# Render history.
for msg in st.session_state.messages:
    role = "assistant" if isinstance(msg, AIMessage) else "user"
    with st.chat_message(role):
        st.markdown(msg.content)

# Capture new input, then rerun so the user's message renders immediately and the
# LLM turn runs on a clean pass (with auto-refresh disabled — see the guard above).
if prompt := st.chat_input(
    "Ask about parking or make a reservation…", disabled=st.session_state.pending
):
    st.session_state.messages.append(HumanMessage(content=prompt))
    st.session_state.pending = True
    st.rerun()

# Process the pending turn. The user's message is already in the rendered history.
if st.session_state.pending:
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            try:
                answer = run_turn(
                    st.session_state.messages, st.session_state.session_id
                )
            except Exception as exc:  # surface config/errors gracefully
                answer = (
                    "⚠️ Something went wrong. Make sure OPENAI_API_KEY is set and "
                    f"the knowledge base has been ingested.\n\n`{exc}`"
                )
        st.markdown(answer)
    st.session_state.messages.append(AIMessage(content=answer))
    st.session_state.pending = False
    # Rerun so the idle auto-refresh timer is re-mounted (it was skipped while
    # pending) and notification polling resumes.
    st.rerun()
