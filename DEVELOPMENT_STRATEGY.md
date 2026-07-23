# CampusBrain Agent — Development Strategy

**Reprioritized 2026-07-23 for MVP-first delivery.**

The goal of this project is to learn how AI agents work by building one from
scratch — not to build an enterprise platform. This document is ordered
accordingly: **22 milestones to a working agent**, everything else deferred
behind an explicit trigger.

Milestone numbers are stable (M1–M48) and match `ARCHITECTURE.md`, `ROADMAP.md`,
and `spike/PROVIDER_EVALUATION.md`. Only the *order* changed. Gaps in the Core
Path numbering are deliberate — the missing numbers are in Deferred.

Assumed structure:

```
backend/app/{core,models,llm,tools,agent,api/v1,eval}
backend/alembic/  backend/tests/  backend/cli.py
spike/            ← M0 only. Deleted at M6.
```

**Milestone template:** Goal · Why it exists · Concepts to learn first ·
Alternatives & trade-off · Files · Definition of Done · Interview angle.

---

## THE CORE PATH — 22 milestones to a working agent

| Phase | Milestones | Outcome |
|---|---|---|
| **A. Foundations** | M0–M4 | ✅ **DONE** |
| **B. LLM (one provider)** | M5, M6, M8 | Typed, testable LLM calls |
| **C. Tools** | M12, M13, M14, M15, M16, M18 | Six tools the agent can call |
| **D. The loop** | M19, M20, M21, M22, M23, M24 | 🎯 **A working agent** |
| **E. Proof** | M25, M17, M39, M41 | It provably solves real tasks |

**Everything runs synchronously and in one process.** `run_agent(goal)` is a
function call. The CLI prints the trace. No async runs, no SSE, no workers, no
queues, no approvals — those are Deferred, each with a trigger.

### Status

| M | Milestone | State |
|---|---|---|
| M0 | Provider evaluation | ✅ decision frozen |
| M1 | Repo bootstrap | ✅ |
| M2 | Config & settings | ✅ 7 tests |
| M3 | Database & Alembic | ✅ Neon, round-trips |
| M4 | `runs` + `steps` tables | ✅ live |
| M5–M25, M39, M41 | | ⬜ 18 remaining |

### One external prerequisite

**M15 is blocked on P1-CHANGE-1** — a ~25-line service-auth addition to Project 1
(`INTEGRATION_CONTRACT.md`). It is the *only* cross-repo dependency in the Core
Path. Do it when M15 comes up, not before.

P1-CHANGE-2 and P1-CHANGE-3 block M17, which is why M17 sits in Phase E rather
than with the other tools — the agent works without it.

---

## PHASE A — Foundations ✅ DONE

M0 (provider evaluation), M1 (repo), M2 (config), M3 (database), M4 (schema).
See `spike/PROVIDER_EVALUATION.md` for M0's findings and the frozen decision:
**primary `gemini-2.5-flash`**, fallback deferred.

> `tenant_id` exists on both tables and is inert. It was ~10 lines when the
> tables were written and would be a migration touching every query later.
> Nothing reads it; nothing will until multi-tenancy is actually needed.

---

## PHASE B — LLM integration, one provider

### M5 — FastAPI skeleton + health
- **Goal**: App boots; `/health` returns 200; `/health/deps` checks Neon separately.
- **Why it exists**: Somewhere to hang the API at M23, and a smoke test that the app assembles.
- **Concepts**: Liveness vs readiness — a liveness probe that checks dependencies kills a healthy process when a dependency blips. Project 1 kept `/health` deliberately dependency-free ([`main.py:40`](../CollegeRag/backend/app/main.py#L40)).
- **Alternatives & trade-off**: One health endpoint that checks everything — simpler, and it causes restart loops under partial outage.
- **Files**: `app/main.py`, `app/api/v1/health.py`.
- **DoD**: `/health` returns 200 with the database stopped; `/health/deps` reports the failure without the process dying.
- **Interview angle**: "Why should a health check not check the database?"

### M6 — `LLMProvider` protocol and types
- **Goal**: `Completion`, `ToolCall`, `Usage`, and a `Protocol`. No implementation.
- **Why it exists**: The agent loop must never contain `if provider ==`. Also the milestone where `spike/` is deleted.
- **Concepts**: `typing.Protocol` (structural typing, no inheritance); designing an abstraction from *measured* differences rather than anticipated ones.
- **Alternatives & trade-off**: Skip the abstraction entirely for one provider — genuinely tempting, and wrong here for one specific reason: M0 proved free-tier quota exhaustion is routine (we hit it during the spike). When it bites, adding a second provider behind this protocol is ~40 lines; without it, it is a refactor of the loop.
- **Files**: `app/llm/base.py`.
- **DoD**: Every field traces to a difference recorded in `PROVIDER_EVALUATION.md`. Nothing speculative. `spike/` deleted; `results/` and the findings doc preserved.
- **Interview angle**: "When is an interface with one implementation justified?"

### M8 — Gemini provider
- **Goal**: Implement the protocol against `generateContent`, including JSON Schema → OpenAPI-subset translation.
- **Why it exists**: The agent's brain. M0 froze `gemini-2.5-flash`: 100% format compliance, 100% selection, lowest token cost measured.
- **Concepts**: Function calling on the wire — the model emits *text*, the provider parses it. Gemini's schema dialect (no `$ref`/`$defs`/`additionalProperties`); `systemInstruction` as a separate field; role `model` not `assistant`; `args` as a real object, not a JSON string.
- **Alternatives & trade-off**: `google-generativeai` SDK — handles translation for you, adds a second HTTP stack for one endpoint, and hides the wire format M0 spent a day measuring. The translation is ~30 lines and the spike already wrote it.
- **Files**: `app/llm/gemini.py`, `tests/test_gemini_schema.py`.
- **DoD**: Returns a valid `Completion` for text and tool-call responses. Enum, array, and nested-object schemas all survive translation (M0/E6 proved they do). A 429 raises a typed, catchable error — not a bare `HTTPStatusError`.
- **Interview angle**: "Your tool schema works on one provider and 400s on another. Why?"

> **M7 (OpenRouter), M9 (capability config), M10 (prompted-JSON adapter), and
> M11 (router/fallback) are Deferred.** M0's justification: all five tested
> models support native tool calling, and only 3 format failures occurred in
> 180 calls — the repair loop is a safety net, not load-bearing.

---

## PHASE C — Tools

### M12 — Tool contract and registry
- **Goal**: `Tool`, `ToolResult`, decorator registration, JSON Schema auto-derived from a Pydantic args model.
- **Why it exists**: Adding a capability must touch one file and never `app/agent/`.
- **Concepts**: **Tool descriptions ARE the selection algorithm.** M0 proved this concretely — every one of the 15 wrong tool choices traced to a single word ("FIRST") in one description. One Pydantic model serves three jobs: prompt schema, runtime validation, human docs.
- **Alternatives & trade-off**: A plugin system with dynamic discovery — speculative for six tools; a decorator and an import suffice.
- **Files**: `app/tools/{base,registry}.py`.
- **DoD**: A registered tool produces a schema Gemini accepts. Descriptions state **what it does, when to use it, and what it returns** — and none of them contains the word "FIRST" (M0/F7).

### M13 — Tool executor
- **Goal**: Validate args → enforce timeout → catch everything → return `ToolResult`.
- **Why it exists**: **No tool may raise.** Every failure becomes an observation the agent can reason about.
- **Concepts**: Failure-as-data vs exceptions. Distinguishing *unavailable* (retry later) from *failed* (replan without it) — M0/F9 learned this the hard way when a broken harness reported five 429s as five model failures.
- **Alternatives & trade-off**: Let exceptions propagate and catch in the loop — fewer lines, but the loop then needs per-tool knowledge to build a useful observation, which is the coupling the registry exists to prevent.
- **Files**: `app/tools/executor.py`, `tests/test_executor.py`.
- **DoD**: A deliberately broken tool yields `ToolResult(ok=False, ...)` and the run continues. Timeout, HTTP error, validation failure, and quota exhaustion all produce distinct structured errors.
- **Interview angle**: "Why should a tool failure be a value rather than an exception in an agent?"

### M14 — `calculator`
- **Goal**: AST-validated arithmetic. Not a Python sandbox, and not named like one.
- **Why it exists**: Goal B (CGPA eligibility) needs arithmetic over retrieved numbers. Models cannot do reliable arithmetic.
- **Concepts**: AST allow-listing; why `eval` with empty `__builtins__` is **still** escapable (`().__class__.__bases__[0].__subclasses__()`); restricting an interpreter vs isolating one.
- **Alternatives & trade-off**: `RestrictedPython` — better than raw `eval`, still escapable, and a half-secure sandbox that *feels* safe is the worst outcome. A real sandbox (Docker/E2B) is the right answer for general Python and is deferred until a task provably needs it.
- **Files**: `app/tools/calculator.py`, `tests/test_calculator.py`.
- **DoD**: `"7.4 >= 6.5"` → `True`. Tests reject `__import__`, `().__class__`, attribute access, subscripts, comprehensions, and oversized `Pow`. **This milestone is not simplified** — security is not an MVP trade.
- **Interview angle**: "How do you safely execute model-generated code?"

### M15 — Project 1 client — **blocked on P1-CHANGE-1**
- **Goal**: `KnowledgeClient.search()` against P1 using `X-API-Key`.
- **Why it exists**: The single module that knows Project 1 exists.
- **Concepts**: The anti-corruption layer — P1's `SearchHit` is *mapped* to P2's own `Passage` at the boundary, never imported.
- **Alternatives & trade-off**: Import P1's schemas directly — zero mapping code, and it welds the two repos together permanently, defeating the premise of the project.
- **Files**: `app/tools/knowledge.py`.
- **DoD**: A live search returns `Passage[]`. No P1 type or import crosses into `app/agent/`. P1 unreachable → structured error, never an exception.

### M16 — `knowledge_search` tool
- **Goal**: Register retrieval with a description precise enough to drive correct selection.
- **Why it exists**: The agent's entire view of the knowledge base.
- **Concepts**: Writing a description that includes **what it returns** — "returns fragments, not whole documents" is what stops the model using it for summarisation.
- **Alternatives & trade-off**: Wrap P1's `/chat` instead — rejected. It is a complete second LLM agent; calling it means LLM-over-LLM, double latency, and the evidence (scores, page numbers) discarded before the loop sees it.
- **Files**: extends `app/tools/knowledge.py`.
- **DoD**: A real Sitare question returns scored passages with document and page attribution. P1 could swap its entire implementation without changing a line here.

### M18 — `web_search`
- **Goal**: Tavily-backed search returning extracted content.
- **Why it exists**: Goal C needs information outside the corpus.
- **Concepts**: Why Tavily over Brave/SerpAPI — it returns LLM-ready extracted content instead of link lists, removing a second fetch-and-parse round trip.
- **Alternatives & trade-off**: `web_read` (fetch an arbitrary URL) is **deferred** — it is the highest-risk tool in the design (SSRF plus attacker-controlled prompt injection) and no MVP goal requires it.
- **Files**: `app/tools/web_search.py`.
- **DoD**: A live search returns results. Quota exhaustion returns a structured observation the agent can work around, not a crash.

---

## PHASE D — The agent loop 🎯

### M19 — Step budget
- **Goal**: `max_steps` and a wall-clock ceiling. That is all.
- **Why it exists**: An agent without a step limit loops forever. This is the single cheapest bug-prevention in the project.
- **Concepts**: Why agent cost grows **super-linearly** with steps — both the trace and the tool schemas are re-sent every turn.
- **Alternatives & trade-off**: Full token/cost accounting — deferred. M0 measured Gemini at ~324–960 tokens/call and p50 ~1.0 s, so a 15-step run is ~15 s and ~5–15k tokens. Not a number worth policing yet; a runaway loop is.
- **Files**: `app/core/budget.py`.
- **DoD**: A deliberately looping agent halts at 15 steps with a partial answer.

### M20 — Prompt assembly and observation framing
- **Goal**: System prompt, tool schemas, trace rendering, `<observation trusted="false">` fencing.
- **Why it exists**: Tool output is data, never instruction.
- **Concepts**: Indirect prompt injection. Framing is **defence in depth, not a solve** — which is precisely why no effectful tool exists in the MVP. Every tool is read-only, so injection can produce a wrong answer but not a wrong *action*.
- **Alternatives & trade-off**: Sanitising regexes as P1 does — adequate for a chatbot where injection changes words, not for an agent where it could change actions.
- **Files**: `app/agent/prompts.py`.
- **DoD**: Every prompt lives in this one file — none inline in the loop, so they can be diffed as a unit.

### M21 — Selector
- **Goal**: One LLM call → `(thought, tool_call)`. Handles "no tool chosen" and "unknown tool" as recoverable.
- **Why it exists**: The decision-making core.
- **Concepts**: **Format failure vs selection failure** — the former is fixable by engineering, the latter is not. M0's taxonomy (`spike/classify.py`) becomes production code here.
- **Alternatives & trade-off**: Tool-name fuzzy matching — M0 observed zero hallucinated tool names, so it would mask a signal that is not there.
- **Files**: `app/agent/selector.py`.
- **DoD**: Each M0 failure class has an explicit code path; none raises. `MULTI_CALL` never occurred in 180 calls, so one tool per turn is enforced.

### M22 — Trace persistence
- **Goal**: Every step written to `steps` before the next begins.
- **Why it exists**: This is the "basic conversation memory" and "simple logging and debugging" in one table. The prompt is assembled from it, the CLI renders it, and evaluation reads it.
- **Concepts**: One table, four jobs. Append-only. Why per-step commits beat one transaction per run.
- **Alternatives & trade-off**: Keep the trace in memory only — simpler, and you lose the run's history on any crash plus the eval dataset entirely.
- **Files**: `app/repositories/{run,step}_repository.py`.
- **DoD**: After a run, the full trace is queryable in order from Neon.

### M23 — The loop
- **Goal**: `run_agent(goal) -> RunResult`, synchronous. Terminates on `final_answer` or budget.
- **Why it exists**: **This is the agent.**
- **Concepts**: ReAct (Yao et al.) — why interleaving reasoning and acting beats reason-then-act. Termination conditions. M0/E5 confirmed multi-turn works via a plain assistant/user exchange, so native `role:"tool"` messages are not required.
- **Alternatives & trade-off**: Plan-and-execute first — more structure, but it hides the loop mechanics behind a plan, and the loop is the thing being learned. Planning is the first thing added *after* the MVP works.
- **Files**: `app/agent/loop.py`.
- **DoD**: A goal produces a complete answer through autonomous tool use. Terminates correctly on `final_answer`, on budget exhaustion, and on unrecoverable error.
- **Interview angle**: "Walk me through what happens between a user's goal and the first tool call."

### M24 — CLI
- **Goal**: `python cli.py "goal"` prints a Rich-rendered trace as it happens.
- **Why it exists**: The product. The loop gets rewritten many times; a terminal iterates in seconds.
- **Concepts**: Reading a trace as a debugging artifact — where a run goes wrong is usually visible three steps before it fails.
- **Alternatives & trade-off**: A web UI — deferred. The trace format has to stop changing first.
- **Files**: `cli.py`.
- **DoD**: Thought, tool call, observation, and final answer are visually distinct at a glance.

---

## PHASE E — Proof it works

### M25 — Goal B green
- **Goal**: *"My CGPA is 6.2. Do I still qualify for the scholarship, and how far short am I?"* — end to end, correct, cited.
- **Why it exists**: Proves retrieval + reasoning + computation compose.
- **DoD**: **You now have a real autonomous agent.** A legitimate stopping point.

### M17 — `knowledge_list_documents` + `knowledge_read_document` — **blocked on P1-CHANGE-2/3**
- **Goal**: Document inventory and whole-document text.
- **Why it exists**: Whole-document summarisation is impossible with top-k fragments. Placed *after* M25 because the agent works without it.
- **Concepts**: Three tools over one corpus is fine where two were not — they return different *kinds* of thing (inventory, document, fragments), which descriptions can disambiguate. M0/F7 proved the descriptions must be written carefully or they cannot.
- **Files**: extends `app/tools/knowledge.py`.
- **DoD**: Full-document reads are bounded (page range or size cap). **Re-run the M0 selection measurement afterwards** — M0/F7 predicts this is where confusion appears.

### M39 — Golden set
- **Goal**: The 12 labelled goals from `spike/fixtures.py`, promoted to a test suite with expected tool sequences.
- **Why it exists**: Without it, "better" is an opinion.
- **Concepts**: Why agent evaluation is harder than model evaluation — the *trajectory* matters, not just the answer, and the same goal has several correct paths.
- **Alternatives & trade-off**: Outcome-only scoring — blind to an agent that reached the right answer through six wasted calls.
- **Files**: `app/eval/golden.yaml`.
- **DoD**: Runs as pytest. Labels were written before observing model output (already true — they came from M0).

### M41 — Basic metrics
- **Goal**: Task success rate, tool-selection accuracy, average steps.
- **Why it exists**: Turns "seems better" into a number, and closes the loop on M0/F7 — did fixing the tool description actually help?
- **Alternatives & trade-off**: LangSmith/Langfuse — real products, and they would obscure that the `steps` table already contains everything needed.
- **Files**: `app/eval/metrics.py`.
- **DoD**: Computed entirely from the trace table. **No separate instrumentation exists anywhere in the codebase.**
- **Interview angle**: "How do you evaluate an agent when the same goal has many correct paths?"

---

## DEFERRED

Not cancelled — each has a trigger that revives it. Nothing here is needed for
an agent that solves real tasks.

### Next, once the MVP is stable
| M | Item | Trigger |
|---|---|---|
| M26–M28 | Planner + replanning | The loop wanders on multi-step goals. Highest learning value of anything deferred. |
| M7, M11 | Second provider + fallback router | Gemini quota exhaustion blocks development. ~40 lines behind M6's protocol. M0 says this *will* happen. |
| M18b | `web_read` | A goal needs a specific URL read. Bring the SSRF test suite with it. |

### Later
| M | Item | Trigger |
|---|---|---|
| M29–M30 | Reflection | Runs fail slowly, repeating the same mistake |
| M32–M33 | Async runs + SSE | A run outlives an HTTP request, or a browser client exists |
| M34–M35 | Heartbeat, reaper, resumption | Deployed somewhere that restarts |
| M36 | Trace compaction | A run overflows the context window |
| M37 | Session memory | Follow-up goals need prior context |
| M38 | Approval gates | **The first effectful tool.** Not before — and not before M43. |
| M43 | Injection red-team | Gates M38. No effectful tool ships until it passes. |
| M42 | LLM-as-judge | Answer *quality* needs scoring, not just trajectory |
| M44–M46 | Docker, Render, web UI, hardening | Someone other than you uses it |
| M47–M48 | LangGraph port + verdict | The hand-built loop is fully understood |

### Explicitly not planned
Multi-tenancy · horizontal scaling · distributed architecture · high
availability · advanced caching · queue systems · multi-agent collaboration ·
cost optimisation · enterprise security.

`tenant_id` already exists in the schema and stays there — it was ~10 lines
when the tables were written and would be a migration touching every query
later. It is inert and nothing reads it.

---

## What changed in this reprioritization

**Cut from the Core Path:** the provider router and prompted-JSON adapter
(M0 showed native tool calling works on all five models tested), async
execution and SSE (a synchronous function call is a better teaching artifact),
planning and reflection (build the loop first, then see whether planning
*measurably* helps), human approval (every MVP tool is read-only, so there is
nothing to approve), and all deployment work.

**Kept despite being "advanced":** the `LLMProvider` protocol, and the
`calculator` AST validator. The first because M0 proved quota exhaustion is
routine and the protocol makes the fix 40 lines instead of a refactor. The
second because security is not an MVP trade-off.

**Net effect:** 48 milestones → **22 in the Core Path, 4 already done, 18
remaining.** M23 is the agent. M25 proves it.

---

## How to use this document

Work the Core Path top to bottom. Each milestone leaves the system working and
testable — that is the constraint that produced this ordering.

Three milestones are **gates, not checkboxes**:
- **M0** — done; its findings already inverted one architecture decision and exposed a tool-design bug before a line of tool code existed.
- **M25** — first working agent, and an honest place to stop.
- **M43** — deferred, but it permanently gates M38. No effectful tool ships until an injection red-team passes.

Update this file when scope changes during implementation. It is a living
plan — the "what changed" section above exists because measurement already
changed it once.
