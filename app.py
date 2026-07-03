"""Streamlit chat UI for the Astana Central Parking assistant."""
from __future__ import annotations

import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage

from parking_bot import dynamic_db
from parking_bot.agent import run_turn
from parking_bot.config import settings

st.set_page_config(page_title="Astana Central Parking", page_icon="🅿️")

# Ensure the dynamic DB exists (safe/idempotent).
dynamic_db.init_db()


@st.cache_data(show_spinner=False)
def _facts() -> dict:
    return {
        "free": dynamic_db.get_total_free(),
        "hours": dynamic_db.get_working_hours(),
    }


with st.sidebar:
    st.header("🅿️ Astana Central Parking")
    st.caption("RAG assistant — Stage 1")
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

# Render history.
for msg in st.session_state.messages:
    role = "assistant" if isinstance(msg, AIMessage) else "user"
    with st.chat_message(role):
        st.markdown(msg.content)

if prompt := st.chat_input("Ask about parking or make a reservation…"):
    st.session_state.messages.append(HumanMessage(content=prompt))
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            try:
                answer = run_turn(st.session_state.messages)
            except Exception as exc:  # surface config/errors gracefully
                answer = (
                    "⚠️ Something went wrong. Make sure OPENAI_API_KEY is set and "
                    f"the knowledge base has been ingested.\n\n`{exc}`"
                )
        st.markdown(answer)
    st.session_state.messages.append(AIMessage(content=answer))
