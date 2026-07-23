# CampusBrain Agent — Product Vision

## 1. What this is

An **autonomous AI agent runtime** that takes a user's goal in plain language,
decides for itself which tools are needed, executes them in sequence, reasons
over what comes back, and returns a complete answer with its full reasoning
trace visible.

It is not a chatbot. It is not a RAG system. It is a **decision-making loop**
that happens to have a RAG system as one of its tools.

## 2. What this is NOT

| Not this | Because |
|---|---|
| A second RAG pipeline | CampusBrain (Project 1) already owns document ingestion, OCR, chunking, embeddings, vector search, and grounded answering. This project consumes that over HTTP and knows nothing about its internals. |
| A chatbot with plugins | A chatbot answers the message in front of it. An agent decomposes a goal into steps it chooses itself, and may take ten actions before it says anything. |
| A workflow engine | Workflows are authored by humans in advance. Here the *model* authors the sequence at runtime. Hardcoded step order is the thing we are explicitly not building. |
| A LangGraph wrapper | Phase 1 builds the loop in plain Python so the mechanics are understood. LangGraph is introduced later as a comparison, not a foundation. |

## 3. The core philosophy

> The agent knows *that* a tool exists and *what it does*. It never knows *how*.

The agent's entire view of the knowledge base is:

```
knowledge_search(query: str, top_k: int) -> list[Passage]
```

It does not know that behind that call sit PyMuPDF, PaddleOCR, recursive
chunking, Gemini embeddings, Qdrant, Postgres full-text search, and Reciprocal
Rank Fusion. If Project 1 replaced every one of those tomorrow, this project
would not change by a single line.

That is the test for every tool we add: **could Project 1 swap its
implementation without breaking us?** If not, the abstraction is leaking.

## 4. The problems it solves

Three real goals, taken from the vision brief, and what each one demands:

### Goal A — cross-source comparison
> "Summarize the hostel rules from the uploaded documents, compare them with
> the latest hostel notice on the university website, then draft an email
> explaining the differences."

Demands: whole-document retrieval (not top-k fragments), live web fetch, a
synthesis step that holds two sources side by side, and a *drafting* action
that must be shown to a human before it goes anywhere.

### Goal B — retrieval plus computation
> "Read the placement policy, calculate whether my CGPA satisfies the
> eligibility criteria, and explain the result."

Demands: retrieval, extraction of a numeric threshold from prose, safe
arithmetic, and an explanation that cites the policy it applied.

### Goal C — open-ended research
> "Research the latest AI internship opportunities, compare them with my
> university eligibility requirements, and create an application checklist."

Demands: web search whose results cannot be predicted in advance — so the plan
must be revised mid-run. This is the goal that proves a static plan is not
enough.

## 5. What "done" looks like

A user submits a goal. They watch, in real time, the agent think:

```
[plan]     3 steps: retrieve policy → compute eligibility → explain
[think]    I need the placement policy's CGPA threshold.
[tool]     knowledge_search(query="placement eligibility CGPA minimum")
[observe]  3 passages, best score 0.71, from placement_policy.pdf p.4
[think]    Threshold is 7.0. User's CGPA is 7.4. Compute the margin.
[tool]     calculator(expression="7.4 - 7.0")
[observe]  0.4
[think]    I can answer now.
[final]    You are eligible. The policy requires a minimum CGPA of 7.0
           [1]; yours is 7.4, clearing it by 0.4.
```

Every one of those lines is a durable row in the database. That trace is
simultaneously the UI, the audit log, the debugger, and the evaluation dataset.

## 6. Design principles

1. **The plan is a hypothesis, not a contract.** Observations may invalidate it. Replanning is normal, not an error path.
2. **Tool output is data, never instruction.** Every observation is fenced and framed as untrusted content. A document or webpage must never be able to steer the agent.
3. **Effectful tools require a human.** Reading is free. Acting is gated. This is a security boundary, not a feature.
4. **Every run is bounded.** Steps, tokens, wall-clock, and cost all have ceilings. An agent without a budget is an outage waiting to happen.
5. **State is durable, not in-memory.** A run must survive a process restart, pause for approval, and resume.
6. **Single-tenant today, multi-tenant-shaped throughout.** `tenant_id` is plumbed through every table and every query from day one, resolved by one function that currently returns `1`.
7. **Free tier is a design constraint, not a compromise.** Every choice must survive Render free, Neon free, and throttled free LLM models.

## 7. Relationship to Project 1

```
┌──────────────────────────┐         ┌──────────────────────────────┐
│  CampusBrain Agent (P2)  │  HTTP   │  CampusBrain RAG (P1)        │
│                          │────────▶│  independent Knowledge       │
│  planning, tool calling, │ X-API-  │  Service                     │
│  memory, reflection,     │   Key   │                              │
│  approval, evaluation    │         │  ingest, OCR, embed, index,  │
└──────────────────────────┘         │  hybrid retrieve             │
                                     └──────────────────────────────┘
        owns its own LLM                    owns its own LLM
        (OpenRouter / Gemini)               (OpenRouter, internal)
```

Two repositories. Two deployments. Two databases. One HTTP boundary.

Project 1 does not know Project 2 exists — it exposes a read API and a service
credential, nothing more. Project 2 treats Project 1 as a third-party vendor
that could be swapped for any other retrieval provider.

## 8. Long-term shape

A modular agent platform where adding a capability means adding one file with
one decorator, and where the reasoning core has never been touched to
accommodate it.

Maturity ladder:

| Stage | Capability |
|---|---|
| 1 | Single-goal ReAct loop, read-only tools, CLI |
| 2 | Planning, reflection, durable resumable runs |
| 3 | Human approval gates, effectful tools, memory across sessions |
| 4 | Evaluation harness, cost/latency observability, web UI |
| 5 | LangGraph port and honest comparison against the hand-built loop |
| 6 | Multi-tenant, multi-agent delegation |
