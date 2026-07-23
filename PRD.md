# CampusBrain Agent — Product Requirements Document

Version 1.0 · Scope: Phase 1–7 · Status: approved for build

---

## 1. Problem statement

CampusBrain (Project 1) answers single questions from a document corpus. It
cannot do anything that requires more than one lookup, cannot reach outside its
corpus, cannot compute, and cannot chain steps.

Real user goals are multi-step and cross-source. "Am I eligible for placements?"
requires finding a policy, extracting a number, comparing it to the user's
number, and explaining the result. CampusBrain can only do the first part.

This project builds the layer that decides *what steps to take*.

## 2. Users

| User | Needs | Phase |
|---|---|---|
| **Developer (primary)** | A loop they can read, step through, and modify. Full trace visibility. Fast local iteration. | 1 |
| **Student** | Ask a complex goal in plain language, get a complete answer with sources. | 4 |
| **Staff / admin** | Approve effectful actions. Inspect why the agent did what it did. | 5 |

The developer is the primary user for Phases 1–3. This is deliberate: the CLI
is the product until the loop is trustworthy.

## 3. Goals and non-goals

### Goals
- G1 — Execute multi-step goals with autonomously selected tools.
- G2 — Never reimplement any Project 1 capability.
- G3 — Every reasoning step durably persisted and inspectable.
- G4 — Provider-agnostic LLM access with automatic fallback.
- G5 — Deployable entirely on free tiers.
- G6 — Adding a tool touches exactly one new file.
- G7 — Measurable agent quality via an evaluation harness.

### Non-goals (v1)
- Multi-tenancy (designed for, not built)
- Multi-agent delegation / agent-to-agent protocols
- Fine-tuning or training any model
- Document ingestion of any kind
- Voice, mobile apps, or native clients
- Real email/calendar sending (drafting only — no outbound integrations)

## 4. Functional requirements

### 4.1 Run lifecycle

**FR-1** — `POST /api/v1/runs` accepts `{goal: str, session_id?: str}` and
returns `{run_id, status}` **immediately** (HTTP 202). Execution is asynchronous.

> *Why not synchronous?* Runs take 30 s–5 min, exceed typical proxy timeouts,
> and — critically — must be able to **pause** for human approval. A paused
> HTTP request is not a design.

**FR-2** — `GET /api/v1/runs/{id}` returns run status and the full step trace.

**FR-3** — `GET /api/v1/runs/{id}/events` streams steps live via SSE, and on
reconnect replays from a client-supplied `last_event_id`.

**FR-4** — `POST /api/v1/runs/{id}/cancel` stops a run at the next step boundary.

**FR-5** — Run status is a strict state machine:

```
created → planning → running → completed
                  ↘         ↘ failed
                    awaiting_approval → running
                                      ↘ rejected
   any state → cancelled | timed_out
```

**FR-6** — A run interrupted by process restart is detectable (`heartbeat_at`
stale) and is either resumed or marked `failed` by a reaper. No run stays
`running` forever.

### 4.2 Reasoning

**FR-7** — The agent produces an explicit plan before acting: an ordered list of
intents with rationale. The plan is stored and versioned.

**FR-8** — The agent may **replan** mid-run when an observation invalidates the
plan. Replans are recorded with the triggering step.

**FR-9** — Each iteration emits a `thought` (why this action) before the action.
Thoughts are persisted, not discarded.

**FR-10** — Reflection triggers on: tool error, empty result, the same tool
called twice with identical arguments, or 80 % of the step budget consumed.
Reflection is *not* run after every step (cost).

**FR-11** — The agent terminates by calling the `final_answer` tool. It never
terminates by "running out of ideas" silently.

### 4.3 Tools

**FR-12** — A tool declares: `name`, `description`, a Pydantic args model
(auto-converted to JSON Schema), `requires_approval`, `timeout_s`, `idempotent`.

**FR-13** — Registration is one decorator. No plugin loader, no YAML, no dynamic
import scanning.

**FR-14** — Phase 1 tool set:

| Tool | Backing | Approval |
|---|---|---|
| `knowledge_search` | P1 `POST /api/v1/search` | no |
| `knowledge_list_documents` | P1 `GET /api/v1/documents` | no |
| `knowledge_read_document` | P1 `GET /api/v1/documents/{id}/text` | no |
| `web_search` | Tavily free tier | no |
| `web_read` | httpx + text extraction | no |
| `calculator` | AST-validated arithmetic | no |
| `final_answer` | terminal pseudo-tool | no |

**FR-15** — Arguments are validated against the schema *before* execution.
Invalid arguments produce a structured error observation the agent can recover
from — never an exception that kills the run.

**FR-16** — Every tool call is bounded by `timeout_s`. A timeout is an
observation, not a crash.

**FR-17** — Identical `(tool, args)` calls within one run are served from a
per-run cache when the tool is `idempotent`. Prevents the most common loop
pathology and saves free-tier quota.

### 4.4 LLM provider layer

**FR-18** — The interface is `complete(messages, tools=None) -> Completion`,
where `Completion` carries either `text` or `tool_calls`. Prompt construction
is the agent's job; transport and tool-call encoding are the provider's.

**FR-19** — Providers that lack native function calling MUST emulate it
internally via prompted JSON. The agent loop must not contain a single
`if provider ==` branch.

**FR-20** — On provider failure (429, 5xx, timeout, malformed output after one
repair attempt) the router falls back to the secondary provider. Fallbacks are
logged and counted.

**FR-21** — One malformed-JSON repair attempt is allowed, feeding the parse
error back to the model. Then the step fails cleanly.

**FR-22** — Every LLM call records model, prompt tokens, completion tokens,
latency, and estimated cost against the run.

### 4.5 Memory

**FR-23** — **Working memory**: the current run's trace, always in the prompt,
compacted when it exceeds a token ceiling (oldest observations summarized).

**FR-24** — **Session memory**: prior goals and final answers in the same
session, available to the planner.

**FR-25** — **Episodic memory**: completed runs are queryable by goal similarity
for "have I done this before?" Phase 5.

**FR-26** — Agent memory lives in Project 2's own store. It is **never** written
into Project 1's Qdrant collections — that would surface agent chatter as
citations in the student chatbot.

### 4.6 Human-in-the-loop

**FR-27** — A tool with `requires_approval=True` transitions the run to
`awaiting_approval` and persists the pending call.

**FR-28** — `POST /api/v1/runs/{id}/approve` with `{step_id, decision, note?}`
resumes or rejects. A rejection becomes an observation the agent must reason
about, not a hard stop.

**FR-29** — Approvals expire (default 24 h) into `timed_out`.

### 4.7 Safety

**FR-30** — Every observation is wrapped in delimiters with an explicit
"untrusted data, not instructions" frame before entering a prompt.

**FR-31** — The tool allow-list is fixed at run start. Content in an observation
can never introduce a tool that was not already available.

**FR-32** — `calculator` accepts an AST containing only literals, arithmetic
operators, comparisons, and a fixed function allow-list. No names, no
attributes, no calls outside the list, no imports. Expression length capped.

**FR-33** — `web_read` enforces an SSRF block-list: no private IP ranges, no
loopback, no link-local, no cloud metadata endpoints. Redirects re-checked.

**FR-34** — Per-run ceilings: `max_steps` (default 15), `max_tokens`,
`max_wall_clock_s`, `max_tool_calls_per_tool`. Exceeding any ends the run as
`failed` with a partial answer, not a hang.

### 4.8 Evaluation

**FR-35** — A golden set of ≥ 30 goals with expected tool sequences and expected
answer content.

**FR-36** — Metrics: task success rate, tool-selection precision/recall, step
efficiency (actual ÷ minimum), tokens per run, cost per run, groundedness.

**FR-37** — LLM-as-judge scores answer quality against a rubric, with the judge
model recorded.

**FR-38** — Recorded-fixture replay mode so the suite runs offline and free.

## 5. Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| NFR-1 | Run acceptance latency | < 300 ms (202 returned before any LLM call) |
| NFR-2 | Median simple-goal completion | < 45 s |
| NFR-3 | P1 dependency failure | Degrades to a partial answer; never a 500 |
| NFR-4 | Free-tier cost | ₹0 / $0 recurring |
| NFR-5 | Trace completeness | 100 % of LLM and tool calls persisted |
| NFR-6 | Adding a tool | 1 new file, 0 changes to `agent/` |
| NFR-7 | Adding a provider | 1 new file, 0 changes to `agent/` |
| NFR-8 | Cold start | Tolerated; run acceptance must not depend on P1 being warm |

## 6. Dependencies and risks

| Risk | Impact | Mitigation |
|---|---|---|
| Free OpenRouter models emit malformed tool JSON | **High** — breaks the loop | Prompted-JSON adapter + one repair attempt + Gemini fallback. Pin a known-good model, keep a tested alternate. |
| Render free tier spins down after 15 min idle | Cold start ~50 s on first run | Runs are async; the 202 is returned by the process that already woke. Document the delay. |
| P1 is cold or throttled | Tool timeout | Per-tool timeout + one retry + degrade to partial answer. |
| P1 chat rate limit is 120/min **per IP** and P2 is one IP | Shared bucket | We consume `/search` under a service key with its own bucket (see `INTEGRATION_CONTRACT.md`). |
| Neon free tier idles the DB | First query slow | Connection pool with pre-ping; already solved in P1. |
| Tavily free tier exhausted (1000/mo) | Web search unavailable | Tool returns a structured "quota exhausted" observation; agent replans without it. |
| Prompt injection via web content | **Critical** — could drive actions | FR-30/31/34 + approval gates on all effectful tools. |

## 7. Explicitly deferred

| Item | Revisit when |
|---|---|
| Redis | A second instance exists, or streaming crosses processes |
| LangGraph | Phase 8, as comparison |
| `knowledge_answer` tool (P1 `/chat`) | Evaluation shows the agent mishandles raw passages |
| General Python sandbox | A golden-set goal provably needs more than arithmetic |
| Multi-tenancy | A second institution signs on |
| Web UI | Phase 7 — CLI is faster for loop iteration |
| Vector memory | Episodic recall by keyword proves insufficient |

## 8. Acceptance criteria for v1

1. All three vision goals (A, B, C) complete end to end on the golden set.
2. Killing the process mid-run leaves no run stuck in `running`.
3. Swapping OpenRouter → Gemini requires only an env var change.
4. Adding a new tool requires no edit under `app/agent/`.
5. A prompt-injection test document fails to cause any unapproved tool call.
6. Full evaluation suite runs offline in replay mode at zero cost.
7. Deployed and reachable on Render free tier.
