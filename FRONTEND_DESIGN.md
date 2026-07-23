# Frontend Design — Developer-Facing Agent Console

Design only. No code until this is agreed.

Target user: **you and other AI engineers.** The reasoning trace is the
product, not a hidden detail. A student-facing build with a "show reasoning"
toggle comes later and is explicitly out of scope here.

Stack: **React + Vite + TypeScript on Vercel**, backend FastAPI on Render —
mirroring Project 1, so its deployment lessons transfer.

Companions: [MASTERGUIDE.md](MASTERGUIDE.md) ·
[PROVIDER_ARCHITECTURE.md](PROVIDER_ARCHITECTURE.md) ·
[DEPLOYMENT.md](DEPLOYMENT.md)

---

# PART 1 — THE RUN VIEW

The screen that matters. Everything else is a gate you pass once.

## 1.1 The two problems that actually make this hard

**① Silence is indistinguishable from breakage.**
A real `knowledge_search` step took **6,888 ms**. Six seconds of nothing is
exactly when a user concludes it has hung. A spinner does not help — a spinner
says "wait" without saying *for what* or *how long*.

**The fix:** the in-flight step is always visible, always names what it is
waiting on, and shows a **ticking elapsed counter**. Not a spinner.

```
  ⋯  knowledge_search   query: "minimum CGPA scholarship"        4.2s
```

That single line answers "is it stuck?", "what is it doing?", and "how long
has it been?" — the three questions a spinner refuses to answer.

**② A trace row is not a mental unit.**
The backend records `tool_call` and `observation` as two rows, correctly — they
happen at different times and one may never arrive. But a human reads *"it
searched and got 5 results"* as **one action**.

**The fix:** the UI pairs them into a single collapsible block. Two rows in the
database, one card on screen.

## 1.2 Layout

```
┌────────────────────────────────────────────────────────────────────────┐
│ ← Runs          Run 105          ● running        12.4s   4,834 tok    │
│ gemini · gemini-3.1-flash-lite · BYOK                                  │
├────────────────────────────────────────────────────────────────────────┤
│ GOAL                                                                   │
│ My CGPA is 6.2. Look up the minimum Sitare requires for a scholarship  │
│ and calculate how far short I am.                                      │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  1  ▸ 🔍 knowledge_search                              ✓  5 · 6.9s     │
│       query: "minimum CGPA for scholarship"                            │
│       ┌──────────────────────────────────────────────────────────┐     │
│       │ [1] (document 9, page 1, score 0.03)                     │     │
│       │ To retain scholarship each year: maintain CGPA ≥ 6.5;    │     │
│       │ at least 90% attendance per course…                       │     │
│       │                                     ⌄ show all 5 passages │     │
│       └──────────────────────────────────────────────────────────┘     │
│                                                                        │
│  2  ▸ 🧮 calculator                                    ✓  0.001s       │
│       expression: "6.5 - 6.2"        →  0.2999999999999998             │
│                                                                        │
│  3  ⋯ thinking…                                              2.1s      │
│                                                                        │
├────────────────────────────────────────────────────────────────────────┤
│ ANSWER                                                                 │
│ To retain your scholarship you must maintain a minimum CGPA of         │
│ 6.5 [1, 2, 3]. Since your current CGPA is 6.2, you are 0.3 short.      │
│                                          [copy]  [copy trace as JSON]  │
└────────────────────────────────────────────────────────────────────────┘
```

## 1.3 How each step kind renders

| Kind | Treatment | Rationale |
|---|---|---|
| `thought` | Muted, smaller, no border | Context, not an event. Prominent styling would drown the actions. |
| `tool_call` + `observation` | **One card**, tool icon + name, args inline, status badge, count, latency | The mental unit (§1.1②) |
| `plan` | Numbered list, pinned above the timeline | It is the hypothesis the rest is measured against |
| `reflection` | Amber left border, inline | Something went wrong and the agent noticed — visually distinct from ordinary flow |
| `final` | Bottom panel, always visible | The thing you came for |
| `error` | Red card, full message, never truncated | Truncating the one thing you need to debug is the worst possible cut |

## 1.4 The three observation states, visually distinct

This is the distinction the whole backend is built around
(MASTERGUIDE §4.3), and flattening it in the UI would throw it away.

| State | Badge | Meaning to the reader |
|---|---|---|
| `ok` | **✓ green** + count | worked |
| `ok=false` | **✗ red** | this approach was wrong |
| `unavailable` | **⚠ amber** + "service unavailable" | the approach was fine, the *dependency* was down |

A degraded run gets a banner: *"1 tool was unavailable — the answer may be
incomplete."* Without it, a user reads a web-search fallback as the agent
choosing to ignore the corpus.

## 1.5 Expansion — three depths

A developer tool must go all the way down, but not by default.

1. **Collapsed** — one line: tool, key argument, status, latency
2. **Expanded** — the 600-char preview the SSE stream already carries
3. **Raw** — full JSON from `GET /runs/{id}`, fetched lazily

Depth 3 matters because the stream deliberately sends a *summary* — one
observation was ~5 KB and streaming it whole pushed kilobytes to a browser
drawing one line. The full record stays on the run endpoint, so "show raw" is
a fetch, not a bigger stream.

## 1.6 Timing, shown honestly

- **Per step:** the latency the backend already records
- **Cumulative:** ticking in the header while running
- **In-flight:** live counter on the current step (§1.1①)
- **After completion:** a slim horizontal bar per step, so where the time went
  is visible at a glance

That last one is what turns this from a log into a profiler — and "which step
was slow" is the question a developer actually has.

## 1.7 States

| State | Screen |
|---|---|
| Submitting | Goal echoed immediately, "starting…" — never a blank screen |
| Connecting | "connecting to run 105…" (Render cold start can be ~50 s — say so) |
| Running | Live timeline |
| Completed | Answer panel, timing bar |
| Failed | Red banner with the reason, **plus the partial trace and best-effort answer** |
| Degraded | Amber banner naming which tool was down |
| Reconnecting | "connection lost — resuming…" then continues seamlessly |

**Failure must never blank the screen.** The backend deliberately returns a
partial answer on budget exhaustion; discarding it in the UI would waste work
the agent actually did.

## 1.8 Mobile

The trace is wide by nature. Below ~640 px: cards stack, arguments move under
the tool name, latency moves to a second line, the answer panel becomes a
sticky bottom sheet. Not a priority for a developer tool, but it must not be
*broken* — P1 hit this.

---

# PART 2 — APPLICATION ARCHITECTURE

## 2.1 Structure

```
frontend/
  src/
    api/
      client.ts          fetch wrapper, credentials: 'include'
      runs.ts            createRun, getRun
      stream.ts          EventSource wrapper + reconnection
      providers.ts       catalogue, connection test
    hooks/
      useRunStream.ts    THE core hook: SSE -> step list + status
      useProvider.ts     BYOK config, session-scoped
    components/
      run/               Timeline, StepCard, ToolCall, Observation,
                         AnswerPanel, TimingBar, StatusBadge
      onboarding/        ProviderPicker, KeyForm, ConnectionTest
      shared/            Banner, Expandable, CopyButton
    pages/
      NewRun.tsx  RunView.tsx  RunHistory.tsx  Settings.tsx
    types.ts             mirrors the API shapes
```

## 2.2 The one hook that matters

`useRunStream(runId)` owns everything live:

```
  connect EventSource  ->  /api/v1/runs/{id}/events  (withCredentials)
  event "run"          ->  set goal, provider, model, status
  event "step"         ->  append; EventSource records the id automatically
  event "done"         ->  final status + answer, close
  onerror              ->  browser auto-reconnects with Last-Event-ID
```

**Reconnection needs no client code.** `EventSource` resends `Last-Event-ID`
automatically, and the backend resumes with `WHERE idx > n` — because the trace
is durable. Most SSE clients hand-roll a replay buffer; this one does not need
one, and that is a property of the backend design, not the frontend.

## 2.3 Three constraints the browser imposes

**① `EventSource` cannot send custom headers.** Auth must be the cookie —
which it is. `withCredentials: true` is required, and so is a **real**
`CORS_ALLOWED_ORIGINS`: a wildcard plus credentials is rejected by browsers,
and `app/main.py` already disables credentials when it sees `*` rather than
shipping a pairing that cannot work.

**② The identity cookie is `httpOnly`.** JavaScript cannot read it, by design.
The frontend never inspects identity; it just sends credentials and lets the
server decide. There is no "who am I" endpoint and none is needed.

**③ Rate limits are real.** 10 runs/min, 120 reads/min. The UI must disable
the submit button while a run is starting and surface a 429 as *"you're going
a bit fast"*, not a generic error.

## 2.4 Where the BYOK key lives

The key is sent in the `POST /runs` body. The question is what the browser
holds between runs.

| Option | Verdict |
|---|---|
| **`sessionStorage`** | **Default.** Survives reload, dies with the tab. Matches "saved for this session" honestly. |
| In-memory only | Safest, and re-entering a key on every refresh will make people paste it into a text file instead — worse in practice |
| `localStorage` | Rejected. Persists indefinitely on a shared machine with no expiry. |

The UI must say plainly: *"Stored in this browser tab and sent to the backend
with each run. Not saved on the server."* Vague reassurance here is worse than
no reassurance.

---

# PART 3 — ONBOARDING

Deliberately second. It is a gate you pass once; the run view is every day.

## 3.1 The honest problem with BYOK-first

A stranger must obtain an API key before seeing anything work. That is real
friction, and pretending otherwise leads to a bad first screen.

**Lead with the zero-friction path:**

```
        Connect an AI provider to get started

  ┌──────────────────────────────────────────────────────┐
  │  Gemini      free tier, fast          → get a key    │
  │  Groq        free tier, very fast     → get a key    │
  │  Ollama      runs on your machine — no key needed    │
  │  OpenRouter  many models · ~50 requests/day free     │
  │  OpenAI · Anthropic · GitHub Models · Custom         │
  └──────────────────────────────────────────────────────┘
```

**Ollama needs no key at all.** Burying that sends people hunting for
credentials they did not need. The OpenRouter note is M0/F5 stated plainly —
someone expecting unlimited use will hit the wall in a few runs and blame the
app.

## 3.2 Connection test — three checks, not one

```
  ✓  Connected                     412 ms
  ✓  Tool calling supported
  ✓  Model: gemini-3.1-flash-lite
```

The middle one prevents the worst support case. M0/E2 found models that accept
a `tools` parameter and **silently ignore it** — the agent then appears to
refuse every task, with no error anywhere. Catching that at setup instead of at
step 4 of a real run is the single highest-value thing onboarding does.

## 3.3 Hosted trial stays invisible

When `HOSTED_API_KEY` is unset the backend returns
`400 hosted_unconfigured`. The frontend never renders a trial option in that
case — the future mode exists in the architecture and is simply absent from the
UI until quota ships.

---

# PART 4 — BACKEND GAPS THIS DESIGN NEEDS

The frontend cannot be built without these. All small.

| # | Endpoint | Why | Size |
|---|---|---|---|
| **F1** | `GET /api/v1/runs` — list mine | Run history has no data source. **Must be identity-scoped**, which is also the trigger for making `RunRepository` identity-scoped rather than relying on an endpoint-level check. | ~25 lines |
| **F2** | Add `tokens`, `elapsed_seconds`, `step_count` to `RunView` | The header shows them; they are already columns on `runs` and simply not returned | ~5 lines |
| **F3** | `GET /api/v1/providers` — the catalogue | The picker needs labels, blurbs, key URLs and `supports_tools`. It is already `catalogue.json`; this just serves it. **Never returns keys.** | ~15 lines |
| **F4** | `POST /api/v1/providers/test` | The three-check connection test (§3.2) | ~30 lines |
| **F5** | `POST /api/v1/runs/{id}/cancel` | A run can burn 15 steps with no way to stop it | ~15 lines |

**F1 carries a real security requirement.** A list endpoint that forgets
ownership leaks every run — the same IDOR that was already caught once on the
read endpoints. This is exactly why the repository should enforce scoping by
construction rather than by remembering, and F1 is the moment to do it.

---

# PART 5 — ROADMAP

Each step leaves something usable.

| # | Milestone | Outcome |
|---|---|---|
| **F0** | Backend gaps F1–F5, with `RunRepository` made identity-scoped | The API the frontend needs, and the IDOR class closed structurally |
| **F1** | Vite + React + TS skeleton, API client, types, Vercel deploy | A deployed shell that talks to Render |
| **F2** | **`useRunStream` + Timeline + StepCard** | 🎯 **A live trace in a browser.** The product. |
| **F3** | Observation states, expansion depths, timing bar | The developer tool, rather than a log viewer |
| **F4** | Provider picker, key form, connection test | A stranger can onboard |
| **F5** | New Run, history, settings, mobile | A complete application |
| **F6** | Cancel, copy-trace, keyboard shortcuts | The polish that makes it feel like a tool |

**F2 is the milestone that matters.** After it, the thing is real in a browser.
F0 is unavoidable first; everything from F3 on is improvement.

---

# PART 6 — WHAT I WOULD CHALLENGE

**The trace may be too fast to watch.** Measured mean is 2.3 LLM calls and
~4.5 s per run — barely enough to read. The live view earns its keep on *slow*
runs (Render cold start, a 6.9 s retrieval, a stuck provider), which are
exactly the ones you need to debug. But do not over-invest in animation for a
4-second event.

**"Like LangGraph Studio" is a trap worth naming.** Those tools visualise a
*graph* because their agents are graphs. This agent is a **linear loop** — a
vertical timeline is the honest representation, and forcing a node-graph would
be decoration that implies branching the runtime does not have. If planning
(M26) lands, a plan-versus-actual view becomes meaningful; a DAG still would
not.

**No cancel today is a real gap.** A run can consume its whole budget with no
way to stop it. That is F5 in Part 4 and I would not ship the UI without it.

**Run history needs a decision.** Runs are keyed to a browser cookie. Clear
cookies and the history is gone — with no account there is no recovery. Worth
saying in the UI rather than letting someone discover it.

---

# PART 7 — OPEN QUESTIONS

1. **Dark mode only, or both?** A developer tool can defensibly ship dark-only and halve the styling work.
2. **A component library?** shadcn/ui or Radix would save days but adds a dependency and a house style. Hand-rolled keeps the bundle small — this UI has maybe fifteen components.
3. **Does the run view poll `GET /runs/{id}` as an SSE fallback?** Some corporate proxies still break event streams.
4. **Retention.** Runs accumulate in Neon's free 0.5 GB and observations are large. A retention window should be a decision, not a default.
