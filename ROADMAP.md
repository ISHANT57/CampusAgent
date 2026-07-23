# CampusBrain Agent — Development & Learning Roadmap

48 milestones across 8 phases. Each milestone is one sitting, has a definition
of done, and leaves the repo working.

---

# PART A — PHASE OVERVIEW

| Phase | Theme | Milestones | Ships |
|---|---|---|---|
| 0 | Foundations | M1–M5 | Repo, config, DB, health |
| 1 | LLM provider layer | M6–M11 | Provider abstraction + fallback, proven |
| 2 | Tool layer | M12–M18 | Registry + 6 tools, P1 integration |
| 3 | The ReAct loop | M19–M25 | **First working agent (CLI)** |
| 4 | Planning & reflection | M26–M31 | Plan-execute-replan-reflect |
| 5 | Durability, memory, HITL | M32–M38 | Resumable runs, approvals, memory |
| 6 | Evaluation & observability | M39–M43 | Golden set, metrics, judge |
| 7 | API, UI, deployment | M44–M46 | Public, deployed |
| 8 | LangGraph comparison | M47–M48 | Port + honest verdict |

**The critical path is M6 → M11 → M19.** If free-tier tool calling doesn't
work, everything downstream changes. That risk is retired first, deliberately.

---

# PART B — MILESTONE BREAKDOWN

## Phase 0 — Foundations (M1–M5)

| M | Milestone | Definition of done |
|---|---|---|
| M1 | Repo bootstrap | `backend/` skeleton, `.gitignore`, `.env.example`, `requirements.txt`, git init |
| M2 | Config + settings | `pydantic-settings` loads every env var; missing required var fails at startup, not first use |
| M3 | Database + Alembic | Neon connected, `alembic upgrade head` runs clean |
| M4 | `runs` + `steps` tables | Migration applied; `tenant_id` present on both |
| M5 | FastAPI skeleton + health | `/health` returns 200; `/health/deps` checks Neon + P1 |

**Deliberately skipped:** Docker in Phase 0 (P1's Dockerfile is copy-ready in
M44), auth (no users yet), the frontend.

## Phase 1 — LLM provider layer (M6–M11) ← *risk retired here*

| M | Milestone | Definition of done |
|---|---|---|
| M6 | `LLMProvider` protocol + types | `Completion`, `ToolCall`, `Usage` defined; no implementation yet |
| M7 | OpenRouter provider | Plain text completion works; usage recorded |
| M8 | Gemini provider | Same interface, different vendor |
| M9 | **Native tool calling spike** | Test 4–5 free OpenRouter models + Gemini for tool-call reliability. **Write down the results.** This decides the next milestone's importance. |
| M10 | Prompted-JSON adapter | Text-only provider → `tool_calls`; one repair attempt on parse failure; tested against recorded malformed outputs |
| M11 | Router + fallback | Primary → secondary on 429/5xx/timeout/unparseable. Fallback counter. Kill the primary's key and prove a call still succeeds. |

> **M9 is the highest-value milestone in the project.** It converts the biggest
> unknown into a measured fact before any agent code depends on it.

## Phase 2 — Tool layer (M12–M18)

| M | Milestone | Definition of done |
|---|---|---|
| M12 | `Tool` + `ToolResult` + registry | Decorator registration; JSON Schema auto-derived from Pydantic args model |
| M13 | Executor | Validate args → timeout → catch-all → `ToolResult`. **No tool can raise.** Per-run idempotent cache. |
| M14 | `calculator` | AST allow-list; test suite includes `__import__`, `().__class__`, attribute walks — all rejected |
| M15 | P1 client + service key | `KnowledgeClient.search()` works against P1 with `X-API-Key` (needs P1-CHANGE-1) |
| M16 | `knowledge_search` tool | Returns `Passage[]`; P1 down → `ToolResult(ok=False)`, never an exception |
| M17 | `knowledge_list_documents` + `knowledge_read_document` | Needs P1-CHANGE-2, P1-CHANGE-3 |
| M18 | `web_search` + `web_read` | Tavily wired; SSRF block-list tested against loopback, `169.254.169.254`, private ranges, and a redirect into one |

## Phase 3 — The ReAct loop (M19–M25) ← *first working agent*

| M | Milestone | Definition of done |
|---|---|---|
| M19 | `RunBudget` | Steps, tokens, seconds, cost. Enforced before it's needed. |
| M20 | Prompt assembly + observation framing | System prompt, tool schemas, trace rendering, `<observation trusted="false">` fencing |
| M21 | Selector | LLM → `(thought, tool_call)`. Handles "no tool chosen" and "unknown tool" as recoverable. |
| M22 | Trace persistence | Every step committed before the next begins |
| M23 | **The loop** | `run_agent()` end to end; terminates on `final_answer` or budget |
| M24 | CLI | `python cli.py "goal"` streams a Rich-rendered trace live |
| M25 | Vision Goal B green | *"Read the placement policy, calculate whether my CGPA satisfies eligibility"* completes correctly |

**End of Phase 3 you have a real autonomous agent.** Everything after is
quality, safety, and durability.

## Phase 4 — Planning & reflection (M26–M31)

| M | Milestone | Definition of done |
|---|---|---|
| M26 | Planner | Goal + tool specs → ordered intents with rationale; stored on the run |
| M27 | Plan-aware selection | Selector sees the plan; measurably fewer steps than bare ReAct on the golden set |
| M28 | Replanning | Invalidation detected → new plan version, both retained |
| M29 | Reflection triggers | Error / empty / repeated call / 80 % budget — trigger logic unit-tested without an LLM |
| M30 | Reflector | Critique feeds next selection or forces a replan |
| M31 | Vision Goals A + C green | Cross-source comparison and open-ended research both complete |

> **Measure at M27 and M30.** If planning doesn't beat bare ReAct on step count
> or success rate, that is a finding worth keeping, not a failure to hide.

## Phase 5 — Durability, memory, HITL (M32–M38)

| M | Milestone | Definition of done |
|---|---|---|
| M32 | Async run acceptance | `POST /runs` → 202 in < 300 ms; execution in background |
| M33 | SSE stream + replay | `Last-Event-ID` reconnect replays from `steps` |
| M34 | Heartbeat + reaper | `kill -9` mid-run → run reaches a terminal state, never stuck |
| M35 | Run resumption | Interrupted run resumes from the last committed step |
| M36 | Working-memory compaction | Long traces summarized under a token ceiling; thoughts kept verbatim |
| M37 | Session memory | Prior goals/answers in the same session available to the planner |
| M38 | Approval gates | `requires_approval` → `awaiting_approval`; approve/reject endpoints; rejection becomes an observation the agent reasons about; expiry |

## Phase 6 — Evaluation & observability (M39–M43)

| M | Milestone | Definition of done |
|---|---|---|
| M39 | Golden set | 30+ goals with expected tool sequences and answer assertions |
| M40 | Replay harness | Recorded LLM + tool fixtures; full suite runs offline, zero cost |
| M41 | Metrics | Success rate, tool precision/recall, step efficiency, tokens, cost, groundedness |
| M42 | LLM-as-judge | Rubric-scored answer quality; judge model recorded with every score |
| M43 | Injection red-team | Adversarial document + webpage in the corpus; **zero unapproved tool calls** is the pass bar |

> **M43 is a gate, not a checkbox.** If a planted instruction in a webpage can
> steer the agent, effectful tools do not ship.

## Phase 7 — API, UI, deployment (M44–M46)

| M | Milestone | Definition of done |
|---|---|---|
| M44 | Dockerfile + Render deploy | 1 worker, migrate-then-serve (P1's lessons applied directly) |
| M45 | Web UI | Vite/React on Vercel: goal input, live trace, approval buttons |
| M46 | Production hardening | Rate limits, CORS, secret redaction in traces, structured logs |

## Phase 8 — LangGraph comparison (M47–M48)

| M | Milestone | Definition of done |
|---|---|---|
| M47 | Port the loop to LangGraph | Same tools, same providers, same golden set |
| M48 | **Written verdict** | Side-by-side: lines of code, latency, token cost, debuggability, control. State plainly which parts LangGraph should own and which it should not. |

---

# PART C — LEARNING ROADMAP

## C1. Per-milestone template

Every milestone is written up against this. Non-negotiable — it is the point of
the project.

```
1. Why does this component exist?
2. What problem does it solve? (what breaks without it)
3. Internal working — the mechanism, not the API
4. Alternatives considered
5. Trade-offs of the chosen path
6. Production considerations
7. Security implications
8. Scaling behaviour (what breaks at 100× load)
9. Failure scenarios and their handling
10. Interview questions this milestone answers
```

## C2. Phase learning objectives

### Phase 0 — Foundations
Twelve-factor config; why fail-fast beats fail-late on settings; migrations as
code; liveness vs readiness probes (and why P1 deliberately kept `/health`
dependency-free).

*Interview:* "Why should a health check not check the database?"

### Phase 1 — LLM providers
Function/tool calling: what actually goes over the wire; JSON Schema as the
contract between model and code; structured output failure modes; why
temperature 0 is not determinism; token accounting and cost modelling;
**correlated vs uncorrelated failure** in fallback design; circuit breakers.

*Interview:* "How would you make an LLM call reliable when the provider returns
malformed JSON 5 % of the time?" · "Your fallback is a second model at the same
provider. What's wrong with that?"

### Phase 2 — Tools
Tool design as API design; why descriptions *are* the selection algorithm;
idempotency; timeout hierarchies; the anti-corruption layer pattern (§B7);
SSRF; AST allow-listing vs sandboxing vs `RestrictedPython`, and why you should
never ship the middle one.

*Interview:* "How do you safely execute model-generated code?" · "Why is
`eval` with empty `__builtins__` still not safe?"

### Phase 3 — The ReAct loop
The core paper (Yao et al., *ReAct*); why interleaving beats separate
reason-then-act; context window as a budget; the difference between chat history
and an agent trace; termination conditions and why agents loop forever without
one; append-only state.

*Interview:* "Walk me through what happens between a user's goal and the first
tool call." · "How do you stop an agent from looping?"

### Phase 4 — Planning & reflection
Plan-and-execute vs ReAct vs LLMCompiler; plan as hypothesis vs contract; when
planning *hurts* (exploratory tasks); Reflexion and self-critique; the cost of
every quality mechanism; measuring whether an improvement actually improved
anything.

*Interview:* "When would you not use a planner?" · "How do you know your
reflection step is worth its cost?"

### Phase 5 — Durability, memory, HITL
Long-running work in a request/response world; state machines vs implicit state;
idempotent resumption; at-least-once vs exactly-once; the memory hierarchy and
why the four tiers have different stores; context compaction strategies;
human-in-the-loop as a *security control*.

*Interview:* "Your agent process dies mid-run. What happens?" · "Why is
approval a security boundary rather than a UX feature?"

### Phase 6 — Evaluation & observability
Why agent evaluation is harder than model evaluation (the trajectory matters,
not just the answer); trajectory vs outcome metrics; LLM-as-judge bias and how
to control it; golden sets and replay determinism; indirect prompt injection as
the defining unsolved problem of tool-using agents.

*Interview:* "How do you evaluate an agent when the same goal has many correct
paths?" · "How do you defend an agent against a malicious webpage?"

### Phase 7 — Production
Streaming protocols (SSE vs WebSocket vs polling — and why SSE wins here);
free-tier engineering as a real constraint; secret redaction in traces;
cost ceilings as availability protection.

*Interview:* "Why SSE and not WebSocket?"

### Phase 8 — Frameworks
What LangGraph actually provides (graph state, checkpointing, interrupts) versus
what it hides; when a framework earns its abstraction cost; how to evaluate any
framework against a hand-built baseline you fully understand.

*Interview:* "You built this yourself. When would you reach for LangGraph, and
when wouldn't you?"

## C3. Concept dependency order

```
config/migrations
   └─▶ provider abstraction ──▶ tool calling ──▶ tool registry
                                                     │
                                     ReAct loop ◀────┘
                                         │
              ┌──────────────────────────┼──────────────────┐
              ▼                          ▼                  ▼
          planning                  durability          evaluation
              │                          │                  │
          reflection              memory + HITL      injection defense
              └──────────────┬───────────┘                  │
                             ▼                              │
                      framework comparison ◀────────────────┘
```

Nothing here is learnable out of order. Reflection without a loop to reflect on
is a prompt-engineering exercise, not agent engineering.

---

# PART D — SEQUENCING NOTES

**Do first, out of order if needed:** M9 (tool-calling spike). It is the only
milestone whose result could change the architecture.

**Do not start before its dependency:**
- M15 blocked on P1-CHANGE-1 (service auth)
- M17 blocked on P1-CHANGE-2 and P1-CHANGE-3
- M38 (effectful tools) blocked on M43 (injection red-team) passing

**Deliberately late:**
- Docker (M44) — local dev is faster without it; P1's Dockerfile drops in
- Frontend (M45) — the trace format must stabilize first
- LangGraph (M47) — comparison is worthless before the baseline is understood

**Two natural stopping points** if scope needs to shrink:
- **After M25** — a working autonomous agent with a CLI. Complete and honest.
- **After M43** — a production-quality agent, evaluated and hardened, minus the UI.
