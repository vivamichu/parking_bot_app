# Parking RAG Chatbot — Stage 2 (Human-in-the-Loop Admin Approval)

**Stage 2** adds a **second agent** that escalates every reservation to a **human
administrator** for approval. The visitor's chatbot (Agent 1) collects the
booking and creates a *pending* record; a FastAPI **admin server** lets a human
approve/reject it; the **admin agent** (Agent 2) turns that decision into a
status update and pushes an **asynchronous notification** back to the visitor's
chat — so the two agents cooperate through a shared store with a human in the loop.

---

## Features

| Requirement | How it's met |
|-------------|--------------|
| RAG architecture | LangGraph agent + Milvus Lite vector search over the knowledge base |
| Vector database | **Milvus Lite** (embedded, local file) via `pymilvus` |
| Static vs. dynamic data split | Static knowledge → vector DB; live availability/hours/prices → **SQLite** |
| Collect reservations | Interactive collection + validation of name, surname, plate, period → *pending* record |
| **Second agent (Stage 2)** | **`admin_agent.py`** — a LangChain tool-calling agent that verifies availability, applies the admin's decision, and notifies the visitor |
| **Admin communication (Stage 2)** | **REST API + admin web page** (`admin_server.py`, FastAPI) — the human approves/rejects with one click |
| **Escalation & agent-to-agent link (Stage 2)** | Reservations are escalated by writing a *pending* row; decisions flow back via a **notification outbox** table — the two agents communicate through the shared SQLite DB |
| Guardrails / data protection | **Microsoft Presidio** (pre-trained spaCy NER) PII redaction + sensitivity filtering + prompt-injection/topic filter |
| Tests | **50 `pytest` tests** (≥2 per module, incl. the new admin agent & server) |
| CI/CD | **GitHub Actions** (`.github/workflows/ci.yml`) — matrix on Python 3.11 / 3.12 |

---

## Architecture

```
  VISITOR (Streamlit chat)                 ADMIN (browser)
        │                                        │
        ▼                                        │
  Input Guardrail (injection / confidential filter)
        │                                        │
        ▼                                        │
  Agent 1 — LangGraph ReAct (gpt-4o-mini)        │
        ├── search_parking_info → Milvus Lite (RAG, static)
        ├── get_availability / get_working_hours / get_pricing → SQLite
        └── make_reservation → validate + save PENDING row (with session_id)
        │                                        │
        ▼                                        ▼
  Output Guardrail (PII scrub)          ┌────────────────────────┐
        │                               │  FastAPI Admin Server   │
   "PENDING confirmation"               │  GET  /admin/pending    │◀── views queue
        │                               │  GET  /admin/  (page)   │◀── Approve / Reject
        │                               │  POST /admin/decision   │
        │                               └───────────┬────────────┘
        │                                           ▼
        │                          Agent 2 — admin_agent (LangChain ReAct)
        │                          ├── get_reservation_details
        │                          ├── check_space_availability
        │                          └── apply_decision → set status + queue notification
        │                                           │
        ◀──────── polls notifications outbox ───────┘
   "✅ Reservation #N CONFIRMED"
```

**Two agents, one shared store.** Agent 1 and Agent 2 never call each other
directly. They communicate through SQLite:

- the **`reservations`** table is the *request queue* (a `pending` row = an
  escalation waiting for the admin);
- the **`notifications`** table is the *reply channel* (Agent 2 writes the
  outcome keyed to the visitor's `session_id`; the Streamlit app polls it).

**Data split:**
- **Static → Milvus Lite:** general info, parking details, location, booking
  process, policies (`data/static/*.md`).
- **Dynamic → SQLite:** `parking_spots`, `pricing`, `working_hours`,
  `reservations`, `notifications`.

---

## End-to-end reservation flow

1. **Collect** — the visitor asks to book; Agent 1 gathers first name, last name,
   plate, and period, validates them, and calls `make_reservation`.
2. **Escalate** — a `pending` reservation row is written, tagged with the
   visitor's chat `session_id`. The bot replies *"PENDING confirmation by an
   administrator."*
3. **Review** — the administrator opens `http://localhost:8600/admin/`, sees the
   pending row, and clicks **Approve** or **Reject**.
4. **Decide** — the admin server calls Agent 2 (`handle_admin_decision`). The
   agent maps the decision to a status (`approve→confirmed`, `reject→cancelled`),
   updates the row, and queues a notification for that `session_id`.
5. **Notify** — the visitor's chat (auto-refresh or the *Check for updates*
   button) drains the outbox and shows *"✅ Reservation #N CONFIRMED."*

See the screenshots in [`docs/`](docs/): booking → admin page → confirmation.

---

## Project structure

```
.
├── parking_bot/
│   ├── config.py           # settings from .env
│   ├── dynamic_db.py       # SQLite: availability / hours / pricing / reservations / notifications
│   ├── embeddings.py       # embeddings factory (OpenAI | local HuggingFace)
│   ├── ingest.py           # load → chunk → embed → Milvus Lite
│   ├── retrieval.py        # vector search + sensitivity filter
│   ├── guardrails.py       # Presidio PII + injection/topic guardrails
│   ├── reservation.py      # field validation + pending reservation (+ session_id)
│   ├── agent.py            # Agent 1: LangGraph graph (guards + tools)
│   ├── admin_agent.py      # Agent 2: admin decision agent (Stage 2)
│   └── admin_server.py     # FastAPI admin REST API + web page (Stage 2)
├── app.py                  # Streamlit chat UI (+ async notification polling)
├── data/
│   ├── static/*.md         # knowledge base (incl. one SENSITIVE demo file)
│   └── *.db                # generated (gitignored)
├── tests/                  # pytest suite (50 tests)
├── docs/                   # screenshots + presentation
├── .github/workflows/ci.yml
├── requirements.txt        # full runtime deps
├── requirements-dev.txt    # lean deps for the offline test suite
└── .env.example
```

---

## Setup

Requires **Python 3.11 or 3.12**.

```bash
cd "epam/stage 2"

# 1. Create a virtual environment
python -m venv .venv && source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Guardrails) download the small spaCy model used by Presidio
python -m spacy download en_core_web_sm

# 4. Configure
cp .env.example .env
#   Edit .env: add your OPENAI_API_KEY.
#   To run embeddings WITHOUT an API key, set EMBEDDING_PROVIDER=huggingface.
```

> **Chat model:** both agents use OpenAI `gpt-4o-mini` and need `OPENAI_API_KEY`.
> Embeddings can run locally (`EMBEDDING_PROVIDER=huggingface`), so ingestion,
> retrieval, and the whole test suite work with **no API key at all**.

---

## Usage

Build the indexes once:

```bash
python -m parking_bot.ingest       # build the vector index from the knowledge base
python -m parking_bot.dynamic_db   # seed/upgrade the SQLite schema (reset=True)
```

Then run the two services (separate terminals, same venv):

```bash
# Terminal 1 — the admin REST API + approval page
uvicorn parking_bot.admin_server:app --port 8600

# Terminal 2 — the visitor chatbot
streamlit run app.py
```

Demo:
1. In the chat: *"I'd like to book a parking space."* → answer the prompts → the
   bot creates a **pending** reservation.
2. Open **http://localhost:8600/admin/** → click **Approve** (or **Reject**).
3. Back in the chat, the decision appears automatically (or click
   **📨 Check for updates**): *"✅ Your reservation #N has been CONFIRMED."*

**Admin REST API**

| Method & path | Purpose |
|---------------|---------|
| `GET /admin/pending` | JSON list of pending reservations |
| `GET /admin/` | HTML approval page (Approve / Reject buttons) |
| `POST /admin/decision` | Form endpoint used by the page (redirects back) |
| `POST /admin/api/decision` | JSON API: `{"reservation_id", "decision", "note?"}` |

```bash
# Approve reservation 1 from the command line:
curl -X POST http://localhost:8600/admin/api/decision \
     -H "Content-Type: application/json" \
     -d '{"reservation_id": 1, "decision": "approve"}'
```

---

## Guardrails (data protection)

Three layers prevent exposure of sensitive data:

1. **Sensitivity-filtered retrieval** — knowledge-base files marked sensitive
   (e.g. `_internal_contacts.md`) are **never returned to end users**.
2. **Input guardrail** — blocks prompt-injection and attempts to extract
   confidential internal data before they reach the LLM.
3. **Output guardrail (Presidio)** — scans generated answers and redacts leaked
   PII (phone, email, IBAN, gate codes, card numbers). Falls back to regex if
   spaCy/Presidio is unavailable.

---

## Tests

```bash
pytest            # 50 tests, fully offline (LLM & vector store mocked where needed)
```

- Uses the **lean** `requirements-dev.txt` (no heavy vector-DB / embedding deps).
- The LLM is stubbed; the admin agent's decision **tools** and the FastAPI
  endpoints are tested directly (`fastapi.testclient.TestClient`).
- Isolated SQLite fixtures (`temp_db`, `default_db`) keep every test hermetic.

Stage-2 coverage highlights:
- `test_admin_agent.py` — approve→confirmed, reject→cancelled, unknown decision,
  missing reservation, notification queued to the right session.
- `test_admin_server.py` — pending list, admin page render, JSON + form decision
  endpoints (redirect + status change).
- `test_dynamic_db.py` — session-id persistence, pending queue, status
  transitions, notification outbox round-trip and per-session scoping.

---

## CI/CD

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push / PR:

- matrix over **Python 3.11 and 3.12**;
- installs `requirements-dev.txt` and the spaCy model (regex fallback if it
  fails);
- runs the full `pytest` suite.

---

## Tech stack

Python · LangChain 1.x · LangGraph 1.x · OpenAI `gpt-4o-mini` · Milvus Lite ·
SQLite · **FastAPI + Uvicorn** · Microsoft Presidio · Streamlit · pytest ·
GitHub Actions.
