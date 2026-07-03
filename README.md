# Parking RAG Chatbot — Stage 1

An intelligent chatbot for a parking facility ("Astana Central Parking") built on
a **Retrieval-Augmented Generation (RAG)** architecture with **LangChain** and
**LangGraph**. It answers questions about the facility (general info, location,
prices, hours, live availability), interactively collects reservation details,
and protects sensitive data with a guardrail layer.

---

## Features

| Requirement | How it's met |
|-------------|--------------|
| RAG architecture | LangGraph agent + Milvus Lite vector search over the knowledge base |
| Vector database | **Milvus Lite** (embedded, local file) via `pymilvus` |
| Static vs. dynamic data split (bonus) | Static knowledge → vector DB; live availability/hours/prices → **SQLite** |
| Provide information | Tools for RAG search, availability, working hours, pricing |
| Collect reservations | Interactive collection + validation of name, surname, plate, period → pending record |
| Guardrails / data protection | **Microsoft Presidio** (pre-trained spaCy NER) PII redaction + sensitivity filtering + prompt-injection/topic filter |
| Evaluation | Recall@K, Precision@K, MRR, and latency (`evaluation/`) |
| Tests | 34 `pytest` tests (≥2 per module) |
| CI/CD | GitHub Actions (`.github/workflows/ci.yml`) |

---

## Architecture

```
Streamlit UI
   │
   ▼
Input Guardrail  (prompt-injection / confidential-request filter)
   │
   ▼
LangGraph Agent (gpt-4o-mini)
   ├── tool: search_parking_info  → Milvus Lite (static docs, RAG)
   ├── tool: get_availability      → SQLite (dynamic)
   ├── tool: get_working_hours     → SQLite (dynamic)
   ├── tool: get_pricing           → SQLite (dynamic)
   └── tool: make_reservation      → validate + save pending → SQLite
   │
   ▼
Output Guardrail (PII / sensitive-content leak scan — Presidio)
   │
   ▼
Response
```

**Data split (bonus):**
- **Static → Milvus Lite:** general info, parking details, location, booking
  process, policies (`data/static/*.md`).
- **Dynamic → SQLite:** `parking_spots`, `pricing`, `working_hours`,
  `reservations`.

---

## Project structure

```
.
├── parking_bot/
│   ├── config.py         # settings from .env
│   ├── dynamic_db.py     # SQLite: availability / hours / pricing / reservations
│   ├── embeddings.py     # embeddings factory (OpenAI | local HuggingFace)
│   ├── ingest.py         # load → chunk → embed → Milvus Lite
│   ├── retrieval.py      # vector search + sensitivity filter
│   ├── guardrails.py     # Presidio PII + injection/topic guardrails
│   ├── reservation.py    # field validation + pending reservation
│   └── agent.py          # LangGraph graph: guards + tools
├── app.py                # Streamlit chat UI
├── data/
│   ├── static/*.md       # knowledge base (incl. one SENSITIVE demo file)
│   └── *.db              # generated (gitignored)
├── evaluation/
│   ├── gold_set.json     # 15 labelled questions
│   ├── run_eval.py       # Recall@K / Precision@K / MRR / latency
│   └── report.md         # generated report
├── tests/                # pytest suite (34 tests)
├── docs/                 # presentation
├── .github/workflows/ci.yml
├── requirements.txt
└── .env.example
```

---

## Setup

Requires **Python 3.11 or 3.12**.

```bash
cd "epam/stage 1"

# 1. Create a virtual environment
python -m venv .venv && source .venv/bin/activate      # (or use uv)

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Guardrails) download the small spaCy model used by Presidio
python -m spacy download en_core_web_sm

# 4. Configure
cp .env.example .env
#   Edit .env: add your OPENAI_API_KEY.
#   To run WITHOUT any API key, set EMBEDDING_PROVIDER=huggingface
#   (uses a local sentence-transformers model for embeddings).
```

> **Chat model:** the conversational agent uses OpenAI `gpt-4o-mini` and needs
> `OPENAI_API_KEY`. Embeddings can run locally (`EMBEDDING_PROVIDER=huggingface`)
> so ingestion, retrieval, and evaluation work with **no API key at all**.

---

## Usage

```bash
# 1. Build the vector index from the knowledge base
python -m parking_bot.ingest

# 2. Seed the dynamic SQLite DB (also auto-runs when the app starts)
python -m parking_bot.dynamic_db

# 3. Launch the chatbot
streamlit run app.py
```

Then ask, e.g.:
- *"What are your prices?"*
- *"How many EV spaces are free right now?"*
- *"Where are you located?"*
- *"I'd like to book a parking space."* → the bot collects name, surname, plate,
  and period, then creates a **pending** reservation.

---

## Guardrails (data protection)

Three layers prevent exposure of sensitive data:

1. **Sensitivity-filtered retrieval** — knowledge-base files marked sensitive
   (e.g. `_internal_contacts.md`, containing staff phone numbers, an IBAN, and a
   gate override code) are **never returned to end users**. Only staff tooling can
   pass `include_sensitive=True`.
2. **Input guardrail** — blocks prompt-injection and attempts to extract
   confidential internal data before they reach the LLM.
3. **Output guardrail (Presidio)** — scans generated answers and redacts any
   leaked PII (phone, email, IBAN, gate codes, card numbers).

If Presidio / spaCy is unavailable, PII detection falls back to regex so the
system still works.

---

## Evaluation

```bash
python -m evaluation.run_eval    # writes evaluation/report.md
```

Latest run (local `all-MiniLM-L6-v2` embeddings, 15 questions):

| K | Precision@K | Recall@K |
|---|-------------|----------|
| 1 | 0.867 | 0.700 |
| 3 | 0.600 | 0.933 |
| 5 | 0.467 | 0.967 |

**MRR:** 0.911 · **Latency** p50 ≈ 10 ms, p95 ≈ 394 ms (retrieval).

(Precision naturally decreases with K because most questions have only 1–2
relevant documents; Recall and MRR are the headline retrieval-quality metrics.)

---

## Tests

```bash
pytest            # 34 tests, all offline (LLM & vector store mocked where needed)
```

Coverage: config, dynamic_db, embeddings-backed retrieval (mocked), guardrails,
reservation validation, ingestion, agent graph (guard + routing + full-graph
stub-LLM run), and evaluation metrics.

---

## Tech stack

Python · LangChain 1.x · LangGraph 1.x · OpenAI `gpt-4o-mini` · Milvus Lite ·
SQLite · Microsoft Presidio · Streamlit · pytest · GitHub Actions.
