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

Provider frozen by M0, amended after M8: **`gemini-3.1-flash-lite`** — 100%
format compliance over a full 36/36 sample, the only model to clear the
multi-turn gate, and the only one that never hit a quota wall. See
[spike/PROVIDER_EVALUATION.md](spike/PROVIDER_EVALUATION.md).

## Baseline

`python cli.py eval`, 2026-07-23, all dependencies live (Project 1 deployed on
Render with service auth, Gemini, Tavily, Neon):

| metric | value |
|---|---|
| success rate | **9/9 (100%)** |
| tool selection accuracy | **9/9 (100%)** |
| answer accuracy | **5/5 (100%)** |
| mean step efficiency | 0.96 |
| degraded runs | 0 |
| total tokens | 31,962 |

3 of 12 golden goals are skipped, not failed — they need tools that do not
exist yet (`knowledge_list_documents`, `web_read`). Counting them against the
agent would depress the score for a reason unrelated to its reasoning.

An earlier run of the same suite, taken while Project 1 was undeployed, scored
8/9 success and 0.72 efficiency with 6 degraded runs. The gap between the two
is what `degraded runs` exists to make visible: a low score caused by a
dependency outage is a different problem from one caused by bad reasoning, and
a single averaged number hides both.

## Setup

Python **3.12** — pinned in `backend/.python-version` and matched by the
Dockerfile at M44. The machine default is 3.14, on which `pydantic-core` and
`selectolax` have no wheels.

```powershell
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
copy .env.example .env      # then fill it in
```

**Activate the venv before running anything:**

```powershell
.\.venv\Scripts\Activate.ps1     # prompt gains a (.venv) prefix
python cli.py eval
```

A bare `python cli.py ...` uses the system 3.14 interpreter, which has none of
this project's dependencies and fails with `ModuleNotFoundError: No module
named 'rich'`. Without activating, call the venv interpreter explicitly:

```powershell
.\.venv\Scripts\python.exe cli.py eval
```

## Using the agent

```powershell
python cli.py run "What is the minimum CGPA to keep my scholarship?"
python cli.py run "..." --max-steps 5 --quiet
python cli.py tools          # tool descriptions, exactly as the model sees them
python cli.py runs           # recent runs
python cli.py trace 13       # replay a run's full reasoning trace
python cli.py eval           # golden set + metrics
python verify.py             # end-to-end checks incl. live model calls
python -m pytest -q          # 157 offline tests
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
