# Provider Architecture — Hosted Trial + BYOK

Complete design. No code.

Companion to [MASTERGUIDE.md](MASTERGUIDE.md) and
[DEVELOPMENT_STRATEGY.md](DEVELOPMENT_STRATEGY.md). Findings marked M0/Fn come
from [spike/PROVIDER_EVALUATION.md](spike/PROVIDER_EVALUATION.md).

---

# 1. Decisions

| | 🚀 Trial (hosted) | 🔑 BYOK (primary mode) |
|---|---|---|
| Key | one of ours | theirs |
| Signup | none | none |
| Runs | 2 / day | unlimited |
| Max steps | 4 | 15 |
| Token budget | 25,000 | 120,000 |
| Models | one pinned basic model | any tool-capable model |
| Tools | knowledge + calculator | all six |
| Purpose | onboarding, demos | actual use |

**Four rules that constrain every design choice below:**

1. Hosted keys are reachable from **exactly one code path**, gated on trial mode.
2. A BYOK failure **never** falls back to a hosted key. It reports the reason.
3. Adding a provider of an existing wire format is **configuration**, not code.
4. Nothing above `llm/manager.py` knows which provider served a run.

---

# 2. Capacity — measured, not estimated

From 14 real completed runs in the `steps` table:

```
mean LLM calls per run : 2.3
mean tokens per run    : 4,328
```

Each loop iteration is **one** LLM call producing a `tool_call` + `observation`
pair, so a 5-row trace is 3 calls, not 8. (An earlier estimate in this document
said ~8 and was wrong — it counted trace rows.)

At 2 trial runs per user, capped at 4 steps:

| Hosted provider | Free requests/day | Trial runs/day | Users/day |
|---|---|---|---|
| **Gemini** flash-lite | ~1,500 | ~150+ | **~75** |
| **Groq** | generous, **unmeasured** | likely high | likely high |
| **OpenRouter** | ~50 (M0/F5) | ~21 | **~10** |

**Recommendation: start on Gemini.** M0/F6 measured it directly —
`gemini-3.1-flash-lite` never hit a quota wall across the entire 180-call spike,
while both OpenRouter models and two sibling Gemini models did.

**Before relying on Groq, measure it.** The key is already in `.env` and unused.
A one-hour M0-style probe (E1 connectivity, E2 tool-call support, E5 multi-turn)
would settle whether it beats Gemini as the hosted default. Groq's free tier is
reportedly the most generous of the three, and it is the fastest — but "reportedly"
is not a number, and this project has a rule about that.

**Switching hosted provider is one env var** (§9), so this is a reversible
decision, not a commitment.

---

# 3. Provider Manager

## 3.1 Eight providers, three adapters

The eight are three wire formats:

| Adapter | Serves |
|---|---|
| `openai_compatible.py` | OpenRouter, Groq, GitHub Models, OpenAI, Ollama, custom endpoints |
| `gemini.py` | Google *(exists today)* |
| `anthropic.py` | Claude |

Everything else lives in the catalogue.

## 3.2 The catalogue — providers as data

```
app/llm/catalogue.json

  "groq": {
    "adapter": "openai_compatible",
    "base_url": "https://api.groq.com/openai/v1",
    "auth": "bearer",
    "label": "Groq",
    "blurb": "very fast, generous free tier",
    "requires_key": true,
    "allows_custom_base_url": false,
    "models": [
      { "id": "...", "label": "...", "supports_tools": true }
    ]
  }
```

**Adding Together, Fireworks, DeepSeek, Cerebras = one entry.**
Adding Cohere's *native* API = a new adapter, because it is a different
protocol. That is the honest boundary of "configuration, not code": config
covers new **vendors**, not new **wire formats**.

**`supports_tools` is mandatory.** M0/E2 found models that accept a `tools`
parameter and silently ignore it — the agent then appears to refuse every task,
with no error anywhere. Models without it are never offered.

## 3.3 Resolution

```
resolve(context) -> ResolvedProvider
```

```
1. context has BYOK config?
       → build adapter from their catalogue entry + their key
       → on failure: STOP. Report. Never reach step 2.

2. context.mode == TRIAL and quota available?
       → hosted key, pinned model, trial budget

3. otherwise
       → NoProviderAvailable(reason)   ← a typed reason the UI renders
```

```
ResolvedProvider
  ├── provider : LLMProvider      ← the existing Protocol, unchanged
  ├── mode     : TRIAL | BYOK
  ├── budget   : RunBudget        ← trial gets the reduced one
  └── label    : "Gemini · flash-lite"    ← for the UI and the trace
```

`loop.py` receives `resolved.provider` and `resolved.budget` and cannot tell
whose key paid. Same discipline as `knowledge.py` being the only file that
knows Project 1 exists.

**Resolved once per run, not per call.** A run that switches provider mid-way
produces a trace where half the steps came from a different model — unreadable
when debugging, and it makes token accounting meaningless.

## 3.4 What failover IS and IS NOT allowed

| From → To | Allowed | Visible in trace |
|---|---|---|
| BYOK primary → BYOK fallback | ✅ | `provider_switch` step |
| Hosted primary → hosted fallback | ✅ *(both keys are yours, trial only)* | `provider_switch` step |
| **BYOK → hosted** | ❌ **never** | — |
| Hosted → BYOK | ❌ *(they have no key configured)* | — |

Every permitted switch writes a step. An invisible switch makes latency and
token counts inexplicable when you later read the trace.

## 3.5 What each adapter still owns

Not everything normalises away. M0 measured these, and each adapter handles its
own:

| Concern | Why it stays per-adapter |
|---|---|
| Schema dialect | Gemini takes an OpenAPI 3.0 subset, not JSON Schema |
| Error classification | M0/F2 — two 429s demanding opposite responses, worded differently by every vendor |
| Tool-call shape | OpenRouter returns arguments as a JSON **string**, Gemini as an **object** |
| Usage fields | `usage.prompt_tokens` vs `usageMetadata.promptTokenCount` |

---

# 4. Backend architecture

## 4.1 New and changed modules

```
app/llm/
  base.py                LLMProvider Protocol            (exists, unchanged)
  gemini.py              Gemini adapter                  (exists)
  openai_compatible.py   NEW — six providers
  anthropic.py           NEW
  catalogue.json         NEW — provider definitions as data
  manager.py             NEW — resolve(context) -> ResolvedProvider
                               THE ONLY file that touches hosted keys

app/core/
  context.py             NEW — who is asking (extends resolve_tenant())
  quota.py               NEW — reserve / release / enforce
  redaction.py           NEW — strip credentials before persistence

app/api/v1/
  runs.py                NEW — POST /runs, SSE trace       (M32)
  providers.py           NEW — catalogue, test, configure
```

`loop.py`, `selector.py`, `executor.py`, every tool: **unchanged.**

## 4.2 Data model

```
trial_usage
  identity       TEXT        hash of the signed browser token
  day            DATE        UTC
  runs_used      INT
  tokens_used    BIGINT
  UNIQUE (identity, day)

trial_usage_ip                -- coarse ceiling only, see §5.2
  ip_hash, day, runs_used
  UNIQUE (ip_hash, day)

hosted_usage                  -- ONE row per day. The cost control.
  day, runs_used, tokens_used, provider
```

IPs are hashed — personal data, and only equality is ever needed.

**Changes to `runs`:**

```
+ mode           TEXT     'trial' | 'byok'
+ provider_name  TEXT
+ model          TEXT
+ identity       TEXT     nullable
```

Enough to answer "what served this run" from the trace alone — no new
instrumentation, the same principle the existing metrics follow.

## 4.3 Where BYOK credentials live

**Server-side session store, TTL'd, encrypted at rest. Never in `runs`/`steps`,
never in logs.**

A run makes several provider calls over minutes, so the key must persist across
requests. "Session-only" therefore means *your server holds it for the session*
— say that plainly in the UI rather than implying it never leaves the browser.

| Option | Verdict |
|---|---|
| Session store, TTL, encrypted | **Default.** Matches "save for the current session". |
| Persisted per account | Only once accounts exist, and opt-in |
| Browser localStorage, re-sent per request | Rejected — the key crosses the wire on every call and lands in *more* logs |

---

# 5. Quota system

## 5.1 Layers, cheapest first

| Layer | Trial limit | Enforced |
|---|---|---|
| Per run | 4 steps / 25k tokens | `budget.py` — **already built** |
| Per identity / day | **2 runs** | before the run starts |
| Per IP / day | 20 runs | coarse ceiling — see §5.2 |
| **Global / day** | **config, below the vendor's own limit** | **the control that bounds cost** |
| Vendor account | spend cap (when paid) | the only limit a bug in your code cannot bypass |

**Why the global ceiling sits below the vendor limit:** you want to hit *your*
cap first and fail with a message you wrote, not a 429 mid-run. Leave headroom;
do not tune it up to the vendor's number.

## 5.2 Trial identity is cookie-first, IP second

Sitare students sit behind campus NAT. IP-keyed limits treat the whole
university as one user. Project 1 hit exactly this and documented it:

> "Anonymous callers have no user id, so rate_limit_key falls back to client IP
> — and a whole campus behind one NAT is a single IP. 20/minute would have
> meant 20 questions per minute for the entire college."
> — `CollegeRag/backend/app/api/v1/chat.py`

So: a **signed browser token** is the primary identity; **IP is a coarse
secondary ceiling** set high enough that a computer lab is not locked out.

**Neither is abuse-proof, and that is fine.** Cookies clear, IPs rotate — assume
~10× the intended per-identity limit leaks through. The trial allowance is small
enough that abusing 2 runs is boring, and the global ceiling is what actually
bounds the cost.

## 5.3 Reserve, then reconcile

Decrement **before** the run, not after. A run that dies at step 3 has already
spent those tokens; refunding on failure lets a deliberately-crashing run loop
forever.

Reconcile actual token usage at the end, so the token counter stays honest even
though the run counter was reserved optimistically.

## 5.4 Fail closed

If the quota store is unreachable, **refuse trial runs**. Failing open is how a
bad deploy becomes a bill. BYOK runs are unaffected — they cost you nothing, so
they should keep working when your database has a bad minute.

## 5.5 What to monitor

| Signal | Meaning |
|---|---|
| hosted runs/day vs ceiling | headroom |
| distinct identities/day | organic growth, or automation |
| **p95 tokens per run** | a jump means someone found how to make runs expensive |
| trial → BYOK conversion | whether trial is doing its job |

---

# 6. Frontend onboarding

## 6.1 First open

```
            What should power your agent?

  ┌───────────────────────────┐   ┌───────────────────────────┐
  │  🚀  Try it now            │   │  🔑  Use my own key        │
  │                           │   │                           │
  │  No signup, no key.       │   │  Unlimited runs.          │
  │  2 runs per day.          │   │  Any model you like.      │
  │  Shorter reasoning.       │   │  You pay your provider.   │
  └───────────────────────────┘   └───────────────────────────┘

        Ollama runs on your own machine and needs no key at all.
```

That last line matters. It is the genuinely free *unlimited* path, and burying
it pushes people into trial mode who never needed to be there.

**Show remaining trial runs before they commit** — "2 runs left today" on the
card. Discovering exhaustion after clicking is a worse first impression than
never offering it.

## 6.2 BYOK wizard

**Step 1 — provider.** Cards with honest labels, not a dropdown:

```
  Gemini          generous free tier, fast
  Groq            very fast, generous free tier
  OpenRouter      many models — free tier is ~50 requests/day
  Ollama          runs on your machine, no key needed
  GitHub Models   free with a GitHub account
  OpenAI          paid
  Anthropic       paid
  Custom          any OpenAI-compatible endpoint
```

The OpenRouter note is M0/F5 stated plainly. A user who picks it expecting
unlimited use will hit the wall in a few runs and conclude the app is broken.

**Step 2 — key.** Password field, provider-specific help link. Custom endpoints
also take a base URL, validated per §7.1.

**Step 3 — test.** One real call, reporting three things:

```
  ✓  Connected                     412 ms
  ✓  Tool calling supported
  ✓  Model: gemini-3.1-flash-lite
```

The middle check is the one that prevents support tickets. A model without tool
calling produces an agent that appears to refuse every task.

**Step 4 — fallback (optional, skippable).** "Add a second provider for when the
first is rate-limited."

## 6.3 Persistent status

```
Trial : ⚡ Trial · 1 of 2 runs left today · [Use my own key]
BYOK  : 🔑 Gemini · flash-lite · [Manage providers]
```

## 6.4 The two screens that need real design

**Trial exhausted** — not an error. This is the conversion moment, and the whole
reason trial exists:

```
  You've used both trial runs for today.

  Connect your own provider for unlimited runs. Gemini and Groq
  both have free tiers, and Ollama needs no key at all.

  [ Connect a provider ]          [ Come back tomorrow ]
```

**Their key fails mid-run** — the trace already reports this honestly, because
`unavailable` is a distinct state (MASTERGUIDE §7.4):

```
  ⚠  Your OpenRouter key hit its daily limit at step 3.

  OpenRouter's free tier allows about 50 requests per day, and one
  agent run uses 2–4.

  [ Switch to fallback ]   [ Update key ]   [ See partial answer ]
```

Never a silent switch to the hosted key. That is rule 2, and it is the rule
most likely to be violated by accident, because "just make it work" is the
tempting fix.

---

# 7. Security model

## 7.1 Custom base URL is SSRF ⚠️ *the sharpest new risk*

"OpenAI-compatible endpoint" means the user supplies a URL and **your server
POSTs to it**. Point it at `http://169.254.169.254/…` and the app becomes an
SSRF proxy into your own infrastructure.

**The validator already exists.** `web_read.py` resolves every hostname and
rejects non-public addresses, re-checking after every redirect. The same
function must guard provider base URLs.

**Ollama is the deliberate exception.** It is *meant* to be
`localhost:11434`. That requires an explicit, separately-named allowance — not
a hole in the check — and it is only safe because Ollama runs on the *user's*
machine. **In a hosted deployment there is no user machine, so the exception
must be off by default in production.**

## 7.2 Keys must not reach the trace

`steps` stores tool inputs, outputs and errors, and provider errors echo
request context freely. **Redaction runs on write, not on display** — the
display path is not the one that gets exported, dumped, or shipped to a log
aggregator.

Belt and braces: a test that runs a real BYOK request with a marker key and
asserts the marker appears nowhere in the database.

## 7.3 Provider config must be unreachable by tools

Currently safe because every tool is read-only. If a future tool could read or
change provider settings, prompt injection becomes **key theft**. Provider
configuration is not, and must never become, tool-accessible state.

## 7.4 Model selection is validated, never free text

A user typing an arbitrary model id can pick one without tool support, and the
agent then fails in a way that looks like a bug in your app. Selection comes
from the catalogue; custom ids are allowed but must pass the tool-calling check
before being saved.

## 7.5 Trial abuse

Assume leakage. The defence is not the identity check — it is that the trial
allowance is small (2 runs), the step budget is small (4), and the global
ceiling is hard.

Cheap friction to add **only if abuse appears**: a signed token required to
start a run, issued on page load; proof-of-work on the first run of a day.
Adding it pre-emptively costs conversion for a problem you may not have.

## 7.6 Threat summary

| Threat | Control | Residual risk |
|---|---|---|
| SSRF via custom base URL | reuse `web_read` validator | Ollama exception, prod-disabled |
| Key in logs / trace | redaction on write + marker test | — |
| Key theft via injection | provider config not tool-reachable | holds while tools stay read-only |
| Trial abuse | small allowance + global ceiling | leaks ~10×, bounded by ceiling |
| Cost overrun | global ceiling < vendor limit; vendor spend cap | needs the cap set before paid |
| BYOK → hosted leak | one code path, gated | **must be tested, not assumed** |

---

# 8. Milestones

Each leaves the system working. You can stop after any of them.

### P1 — Provider Manager *(no users, no UI)*
- Three adapters, catalogue as data, `resolve()`, base-URL SSRF validation, redaction on write. BYOK via env var.
- **Valuable alone**: provider switching becomes configuration instead of code — which M0 showed you need.
- **DoD**: `cli.py run --provider groq` works; `loop.py` has no provider-specific branch; a marker key appears nowhere in the database.

### P2 — Runs API *(this is M32)*
- `POST /runs` → 202, SSE trace stream, signed browser identity.
- **Why here**: trial mode needs a request boundary and an identity to count against.
- **DoD**: a browser starts a run and watches the trace; identity survives a reload.

### P3 — Trial mode
- Quota tables, layered enforcement, reserve-then-reconcile, fail-closed, reduced trial budget, hosted provider selected by config.
- **DoD**: the 3rd run in a day is refused with a message, not an error. Global ceiling verified below the vendor limit.

### P4 — BYOK UI *(this is M45)*
- Onboarding choice, provider wizard, connection test, status bar, exhaustion screen.
- **DoD**: a new user reaches a completed run without reading documentation.

### P5 — Accounts
- Signup, persisted encrypted provider config, per-account quota.
- **Trigger**: only if P3 shows people actually hitting the trial wall. Before that it is speculation.

### P0 — Groq probe *(optional, ~1 hour, do it first)*
- Run M0's E1/E2/E5 against Groq. It may be the better hosted default, and the key is already sitting unused in `.env`.
- **DoD**: a row in `PROVIDER_EVALUATION.md` with the same columns as the other five models.

---

# 9. Configuration reference

Free → paid is **six env vars, zero code**:

```bash
# --- Hosted trial ---------------------------------------------------
HOSTED_PROVIDER=gemini              # gemini | openrouter | groq
HOSTED_MODEL=gemini-3.1-flash-lite
HOSTED_API_KEY=...                  # SEPARATE key/project from dev (M0/F6)
HOSTED_FALLBACK_PROVIDER=openrouter # optional spillover, hosted-only
HOSTED_FALLBACK_API_KEY=...

# --- Trial limits (raise these on upgrade) --------------------------
TRIAL_RUNS_PER_IDENTITY=2
TRIAL_RUNS_PER_IP=20
TRIAL_GLOBAL_DAILY_RUNS=100         # keep BELOW the vendor's own limit
TRIAL_MAX_STEPS=4
TRIAL_MAX_TOKENS=25000
TRIAL_TOOLS=knowledge_search,calculator

# --- BYOK -----------------------------------------------------------
BYOK_SESSION_TTL_MINUTES=120
BYOK_ALLOW_CUSTOM_BASE_URL=true
BYOK_ALLOW_LOOPBACK=false           # Ollama; MUST stay false in production
```

**Why a separate `HOSTED_API_KEY`:** quotas are per-model **per key** (M0/F6).
Sharing one key means a demo day exhausts the capacity you develop against, and
you find out mid-run.

---

# 10. Open questions

1. **Trial tool set** — `web_search` also costs Tavily quota (1,000/month free). Restricting trial to `knowledge_search` + `calculator` is proposed above; those two are also the honest demo, since they are what makes this visibly different from a chatbot.
2. **Groq** — worth an hour of measurement before it becomes the hosted default or is dismissed.
3. **Trial run retention** — best onboarding-funnel data, and the most disposable. A retention window should be a decision, not a default.
4. **Ollama in production** — the loopback exception only makes sense locally. `BYOK_ALLOW_LOOPBACK=false` in production is proposed; a local build could differ.

---

# 11. What changed from the first draft

| First draft | Final | Why |
|---|---|---|
| ~8 LLM calls per run | **2.3, measured** | Counted trace rows instead of iterations |
| 3 trial runs/day | **2** | Your call; also stretches capacity further |
| Hosted pinned to one vendor | **Configurable: Gemini / OpenRouter / Groq** | Your call; makes the free→paid switch config-only |
| 9 providers | **8 providers, 3 adapters** | Three wire formats, not nine |
| Per-user limits as cost control | **Global ceiling as cost control** | Per-identity limits shape distribution; only the global cap bounds it |
| IP-based trial identity | **Cookie-first, IP as coarse ceiling** | Campus NAT — Project 1 documented this exact failure |
| Model as free text | **Validated against the catalogue** | M0/E2 — models that accept `tools` and ignore them |
