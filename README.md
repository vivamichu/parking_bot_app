# Parking RAG Chatbot — Stage 4 (Unified LangGraph Orchestration)

The full pipeline for "Astana Central Parking", unified end-to-end:

1. **User interaction** — a RAG chatbot (Agent 1) answers questions and collects
   a reservation.
2. **Administrator approval** — the request is escalated to a **human
   administrator** (Agent 2), a **human-in-the-loop** step.
3. **Data recording** — once approved, the reservation is persisted through an
   **MCP server**.

**Stage 4** ties all of this together with a single **LangGraph orchestration
graph** ([`parking_bot/orchestrator.py`](parking_bot/orchestrator.py)) whose
nodes map one-to-one onto those phases:

```
user_interaction ──▶ admin_approval ──(confirmed)──▶ record ──▶ END
   (RAG chatbot)      (interrupt: HITL)              (MCP)
                            └────────(rejected)──▶ finalize ──▶ END
```

The `admin_approval` node uses LangGraph's `interrupt()` so the graph **pauses**
until a human decides, then **resumes** with `Command(resume=…)` — real
human-in-the-loop control flow, backed by a checkpointer. The whole pipeline is
covered by **integration tests** and **load tests**.

> Stages 1–3 are summarised below; Stage 4 is the orchestration layer on top.

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
| **MCP server (Stage 3)** | **`mcp_server.py`** — official MCP SDK (`FastMCP`), Streamable-HTTP, tool `record_confirmed_reservation` writing `Name \| Car Number \| Reservation Period \| Approval Time` |
| **MCP integration (Stage 3)** | On approval the admin agent calls the server via **`mcp_client.py`**; `recording.py` orchestrates MCP-or-direct writes |
| **Security (Stage 3)** | **Fail-closed bearer-token** auth (constant-time compare), field **sanitisation** vs. pipe/newline injection, fixed output path (no traversal), locked atomic appends |
| **Reliability (Stage 3)** | Direct-write **fallback** if the MCP server is unreachable — a confirmed reservation is never silently dropped |
| **Orchestration (Stage 4)** | **`orchestrator.py`** — one LangGraph graph: `user_interaction → admin_approval (interrupt / HITL) → record`, with a checkpointer for pause/resume |
| **Integration & load testing (Stage 4)** | End-to-end orchestration tests + concurrency/throughput load tests (`scripts/load_test.py`, `tests/test_load.py`) |
| Guardrails / data protection | **Microsoft Presidio** (pre-trained spaCy NER) PII redaction + sensitivity filtering + prompt-injection/topic filter |
| Tests | **77 `pytest` tests** (≥2 per module, incl. orchestration + a live MCP round-trip) |
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
        │                              on CONFIRM ──▶│  MCP client (bearer token)
        │                                           ▼
        │                          ┌──────────────────────────────────┐
        │                          │  MCP Server (FastMCP, Stage 3)    │
        │                          │  tool: record_confirmed_reservation│
        │                          │  → data/confirmed_reservations.txt │
        │                          │  Name | Car | Period | Approved-at │
        │                          └──────────────────────────────────┘
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
5. **Record (Stage 3)** — on a genuine approval, the agent sends the reservation
   to the **MCP server**, which appends
   `Name | Car Number | Reservation Period | Approval Time` to the store file.
   If MCP is disabled or unreachable, it writes the same line directly.
6. **Notify** — the visitor's chat (auto-refresh or the *Check for updates*
   button) drains the outbox and shows *"✅ Reservation #N CONFIRMED."*

See the screenshots in [`docs/`](docs/): booking → admin page → confirmation.

---

## Stage 4 — LangGraph orchestration

Everything above runs as **live services** (the deployable surface). Stage 4 adds
a single **LangGraph graph** that expresses the same pipeline as explicit,
testable control flow — the unified orchestration the brief asks for. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full logic.

**Graph** ([`parking_bot/orchestrator.py`](parking_bot/orchestrator.py)):

| Node | Phase | What it does |
|------|-------|--------------|
| `user_interaction` | Chatbot / RAG | Persists the collected booking as a **pending** reservation |
| `admin_approval` | Human-in-the-loop | Calls `interrupt()` → **pauses** for the admin's decision; on resume sets status + notifies |
| `record` | MCP server | On approval, records the reservation via `recording.record_confirmed` (MCP, with fallback) |
| `finalize` | — | Terminal node for rejected reservations |

Compiled with a **checkpointer**, so the interrupt can be resumed later
(`Command(resume=…)`) — even from another process with a persistent checkpointer.

```python
from parking_bot import orchestrator

started = orchestrator.start_pipeline(
    {"first_name": "Aya", "last_name": "Rai", "car_plate": "342GHB01",
     "start_time": "2026-07-10 09:00", "end_time": "2026-07-10 18:00"},
    session_id="demo",
)
# started["awaiting_admin"] is True — the graph paused at admin_approval.

done = orchestrator.resume_pipeline(started["thread_id"], "approve", reason="ok")
# done["status"] == "confirmed"; done["recorded"] == "Aya Rai | 342GHB01 | … | …"
```

Run the built-in demo (approve + reject paths):

```bash
python -m parking_bot.orchestrator
```

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
│   ├── admin_agent.py      # Agent 2: admin decision agent (calls the store on approve)
│   ├── admin_server.py     # FastAPI admin REST API + web page (Stage 2)
│   ├── reservation_store.py# Stage 3: secure, sanitised, locked file writer
│   ├── mcp_server.py       # Stage 3: FastMCP server + bearer-token auth
│   ├── mcp_client.py       # Stage 3: MCP client used by the admin agent
│   ├── recording.py        # Stage 3: MCP-or-direct recording orchestrator
│   └── orchestrator.py     # Stage 4: unified LangGraph pipeline (3 nodes + HITL)
├── app.py                  # Streamlit chat UI (+ async notification polling)
├── scripts/
│   └── load_test.py        # Stage 4: per-component throughput load test
├── data/
│   ├── static/*.md               # knowledge base (incl. one SENSITIVE demo file)
│   ├── *.db                       # generated (gitignored)
│   └── confirmed_reservations.txt # confirmed-reservation store (generated, gitignored)
├── tests/                  # pytest suite (77 tests)
├── docs/
│   ├── ARCHITECTURE.md     # architecture, agent/server logic, deployment
│   ├── presentation.pptx   # slide deck
│   └── *.png               # screenshots
├── .github/workflows/ci.yml
├── requirements.txt        # full runtime deps
├── requirements-dev.txt    # lean deps for the offline test suite
└── .env.example
```

---

## Setup

Requires **Python 3.11 or 3.12**.

```bash
cd "epam/stage 4"

# 1. Create a virtual environment
python -m venv .venv && source .venv/bin/activate

# 2. Install dependencies (includes the MCP SDK)
pip install -r requirements.txt

# 3. (Guardrails) download the small spaCy model used by Presidio
python -m spacy download en_core_web_sm

# 4. Configure
cp .env.example .env
#   Edit .env: add your OPENAI_API_KEY.
#   To run embeddings WITHOUT an API key, set EMBEDDING_PROVIDER=huggingface.
#   To route confirmed reservations through the MCP server, set MCP_ENABLED=true
#   and a strong MCP_AUTH_TOKEN (see below).
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

Then run the services (separate terminals, same venv):

```bash
# Terminal 1 — the MCP reservation-store server (Stage 3)
export MCP_ENABLED=true
export MCP_AUTH_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
uvicorn parking_bot.mcp_server:app --host 127.0.0.1 --port 8765

# Terminal 2 — the admin REST API + approval page   (same MCP_* env)
uvicorn parking_bot.admin_server:app --port 8600

# Terminal 3 — the visitor chatbot                  (same MCP_* env)
streamlit run app.py
```

> If you skip the MCP server (or leave `MCP_ENABLED=false`), confirmed
> reservations are still written to `data/confirmed_reservations.txt` by the
> direct-write fallback — the MCP server just makes it a proper networked
> service. Set the **same `MCP_AUTH_TOKEN`** in every terminal so the client can
> authenticate to the server.

Demo:
1. In the chat: *"I'd like to book a parking space."* → answer the prompts → the
   bot creates a **pending** reservation.
2. Open **http://localhost:8600/admin/** → click **Approve** (or **Reject**).
3. Back in the chat, the decision appears automatically (or click
   **📨 Check for updates**): *"✅ Your reservation #N has been CONFIRMED."*
4. Inspect **`data/confirmed_reservations.txt`** — the approved booking is there:
   `Aya Rai | 342GHB01 | 2026-07-03 00:00 to 2026-07-20 00:00 | 2026-07-03T09:00:12`

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

## Stage 3 — MCP reservation store

The MCP server ([`parking_bot/mcp_server.py`](parking_bot/mcp_server.py)) is a
real [Model Context Protocol](https://modelcontextprotocol.io) server built on
the official Python SDK's `FastMCP`, served over the Streamable-HTTP transport.

**Tool**

| Tool | Arguments | Effect |
|------|-----------|--------|
| `record_confirmed_reservation` | `name`, `car_number`, `reservation_period`, `approval_time?` | Appends one line `Name \| Car Number \| Reservation Period \| Approval Time` to `data/confirmed_reservations.txt` |

**How it's integrated.** When the admin approves, `admin_agent.apply_decision`
calls [`recording.record_confirmed`](parking_bot/recording.py), which — when
`MCP_ENABLED=true` — connects with [`mcp_client.py`](parking_bot/mcp_client.py)
as an MCP client (bearer token) and invokes the tool. A rejection records
nothing; a re-approval of an already-confirmed booking is not double-written.

**Security & reliability**

- **Fail-closed bearer token** — every request to the MCP endpoint must carry
  `Authorization: Bearer $MCP_AUTH_TOKEN`; with no token set the server rejects
  *all* requests. The comparison is constant-time (`hmac.compare_digest`).
- **Injection-safe writes** — `|` and control/newline characters are stripped
  from every field, so a crafted name or plate cannot forge columns or inject
  extra lines. Over-long and empty required fields are rejected.
- **No path traversal** — the output path is fixed by config; callers never
  supply a path.
- **Concurrency-safe** — a process lock + append-mode writes; each record is a
  single atomic line.
- **Reliable** — if the MCP call fails, `recording` falls back to a direct write
  of the identical line, so a confirmed reservation is never lost.

Run it standalone:

```bash
export MCP_AUTH_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
uvicorn parking_bot.mcp_server:app --host 127.0.0.1 --port 8765
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
pytest            # 77 tests, fully offline (LLM & vector store mocked where needed)
```

- Uses the **lean** `requirements-dev.txt` (no heavy vector-DB / embedding deps).
- The LLM is stubbed; agent tools, FastAPI endpoints, and the LangGraph pipeline
  are tested directly. Isolated fixtures (`temp_db`, `default_db`, `store_path`,
  `pipeline_env`) keep every test hermetic; MCP is disabled by default so the
  suite never touches a live server.

Stage-4 coverage highlights:
- `test_orchestrator.py` — **integration testing of the whole pipeline**: the
  graph has the three required nodes; intake pauses at the HITL interrupt;
  approve → confirmed + recorded + notified; reject → cancelled + nothing
  recorded; invalid booking ends before admin.
- `test_load.py` — **load / concurrency**: 300 concurrent store appends are
  lossless & well-formed; 200 concurrent DB inserts all succeed; many full
  pipelines record exactly once.

Stage-3 highlights (still covered): injection-safe store, fail-closed MCP auth,
MCP-or-fallback recording, and a **live MCP client↔server round-trip**. Earlier
stages remain covered (agents, servers, guardrails, retrieval, DB, config).

---

## Load testing

A standalone script reports throughput per component against isolated storage:

```bash
python scripts/load_test.py                 # defaults (n=1000, 16 workers)
python scripts/load_test.py -n 2000 -w 32   # heavier
python scripts/load_test.py --skip-mcp      # skip the live MCP server section
```

Representative local run (`-n 500 -w 16`, Python 3.14, errors=0 throughout):

| Component | Throughput | Latency |
|-----------|-----------:|--------:|
| Chatbot intake (reservation create → SQLite) | ~1,300 ops/s | 0.8 ms/op |
| Full orchestrated pipeline (intake→approval→record) | ~95 ops/s | 10.6 ms/op |
| MCP store append (direct, locked) | ~7,600 ops/s | 0.13 ms/op |
| MCP server round-trip (live HTTP + MCP session) | ~56 ops/s | 18 ms/op |

Interactive-chatbot latency in production is dominated by the LLM call, not the
pipeline; the DB/record/orchestration paths above are the components under our
control.

---

## CI/CD

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push / PR:

- matrix over **Python 3.11 and 3.12**;
- installs `requirements-dev.txt` and the spaCy model (regex fallback if it
  fails);
- runs the full `pytest` suite (including orchestration + load tests).

---

## Tech stack

Python · LangChain 1.x · LangGraph 1.x · **Model Context Protocol (MCP) SDK /
FastMCP** · OpenAI `gpt-4o-mini` · Milvus Lite · SQLite · **FastAPI + Uvicorn** ·
Microsoft Presidio · Streamlit · pytest · GitHub Actions.
