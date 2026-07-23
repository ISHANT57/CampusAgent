# CampusBrain Agent

An autonomous AI agent runtime. It takes a goal in plain language, decides for
itself which tools are needed, executes them, reasons over the results, and
returns a complete answer with its full reasoning trace visible.

Not a chatbot. Not a RAG system. A **decision-making loop** that happens to
have a RAG system as one of its tools.

## Two projects, one HTTP boundary

```
┌──────────────────────────┐         ┌──────────────────────────────┐
│  CampusBrain Agent (P2)  │  HTTP   │  CampusBrain RAG (P1)        │
│  this repo               │────────▶│  ../CollegeRag               │
│                          │ X-API-  │                              │
│  planning, tool calling, │   Key   │  ingest, OCR, embed, index,  │
│  memory, reflection,     │         │  hybrid retrieve             │
│  approval, evaluation    │         │                              │
└──────────────────────────┘         └──────────────────────────────┘
```

Project 1 is an independent Knowledge Service and does not know this project
exists. This project never touches P1's Postgres, Qdrant, or object storage —
only its HTTP API. Swapping P1 for any other retrieval provider is a one-file
change (`app/tools/knowledge.py`).

## Documents

| File | What it answers |
|---|---|
| [VISION.md](VISION.md) | What this is, and what it deliberately is not |
| [PRD.md](PRD.md) | Requirements (FR/NFR), risks, what's deferred and why |
| [ARCHITECTURE.md](ARCHITECTURE.md) | HLD, LLD, tech stack, and a self-critique |
| [DEVELOPMENT_STRATEGY.md](DEVELOPMENT_STRATEGY.md) | 49 milestones, M0–M48, with definitions of done |
| [ROADMAP.md](ROADMAP.md) | Phase view + the learning roadmap |
| [INTEGRATION_CONTRACT.md](INTEGRATION_CONTRACT.md) | The four changes requested of Project 1, each justified |
| [spike/PROVIDER_EVALUATION.md](spike/PROVIDER_EVALUATION.md) | M0 findings — which models can actually run an agent |

## Status

**Core Path: 22 milestones to a working agent. 4 done, 18 remaining.**

| Phase | Milestones | State |
|---|---|---|
| A. Foundations | M0–M4 | ✅ done |
| B. LLM (one provider) | M5, M6, M8 | ⬜ |
| C. Tools | M12–M16, M18 | ⬜ |
| D. The loop | M19–M24 | ⬜ |
| E. Proof | M25, M17, M39, M41 | ⬜ |

**M23 is the agent. M25 proves it.** Everything runs synchronously in one
process — no async runs, queues, workers, or approval flows. Those are deferred
behind explicit triggers in [DEVELOPMENT_STRATEGY.md](DEVELOPMENT_STRATEGY.md).

Provider decision frozen by M0: **`gemini-2.5-flash`** (100% format compliance,
100% tool-selection accuracy across 180 scored calls).

## Setup

Python **3.12** — pinned in `backend/.python-version` and matched by the
Dockerfile at M44. The default interpreter on the dev machine is 3.14, on which
`pydantic-core` and `selectolax` have no wheels.

```bash
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
cp .env.example .env      # then fill it in
```

## Running the M0 spike

Throwaway code, deleted at M6. Its findings and failure corpus survive.

```bash
cd spike
python providers.py       # E1  connectivity, latency, token profile
python run.py e2          # E2  native tool-call support matrix
python run.py e3          # E3/E4  format compliance + selection accuracy
python run.py e5          # E5  multi-turn (hard gate)
python run.py e6          # E6  schema dialect
python analyze.py         # score every gate, emit M10 test fixtures
```
