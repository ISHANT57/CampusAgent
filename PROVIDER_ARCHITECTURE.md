# Provider Architecture — Trial + BYOK

Design for the two-mode AI provider system. No code yet.

Companion to [MASTERGUIDE.md](MASTERGUIDE.md) and
[DEVELOPMENT_STRATEGY.md](DEVELOPMENT_STRATEGY.md). Findings referenced as
M0/Fn come from [spike/PROVIDER_EVALUATION.md](spike/PROVIDER_EVALUATION.md).

---

## 1. The decision

| Mode | Key | Limits | Purpose |
|---|---|---|---|
| **🚀 Try for Free** | ours (Gemini, §2.0) | 3 runs/day, 6 steps, 40k tokens, one pinned model | onboarding and demos |
| **🔑 BYOK** | theirs | none beyond their provider's | actual use |

**Non-negotiable rules:**

1. A BYOK failure NEVER falls back to the hosted key. It reports the reason.
2. The hosted key is reachable only in Trial mode. There is no other code path to it.
3. Adding a provider of an existing wire format is configuration, not code.
4. Nothing above `llm/manager.py` knows which provider served a run.

---

## 2. Three corrections to the plan

### 2.0 Hosted trial must run on Gemini, not OpenRouter free ⚠️

The stated intent was OpenRouter's free key for hosted mode, no paid tier.
M0/F5 measured what that actually buys:

```
Rate limit exceeded: free-models-per-day.
Add 10 credits to unlock 1000 free model requests per day
```

Uncredited, the cap is ~50 requests/day. A trial run at 6 steps is ~8 LLM
calls, so:

| Hosted on | Trial runs/day, TOTAL | Users/day at 3 runs |
|---|---|---|
| **OpenRouter free** | **~6** | **2** |
| Gemini free | ~150–180 | ~50 |
| OpenRouter + $10 credits | ~125 | ~40 |

Six runs per day is not a trial mode — it is a queue. The second visitor
tomorrow gets "quota exhausted."

Gemini free costs the same (₹0) and gives roughly 30× the capacity. M0/F6
measured it directly: `gemini-3.1-flash-lite` never hit a quota wall across the
entire 180-call spike, while both OpenRouter models and two sibling Gemini
models did.

M0/F4 argues it from the other side too: OpenRouter's `gpt-oss-20b` had a 6.1 s
median versus Gemini's ~1.0 s. A trial user's first impression would be a
90-second run.

**Decision: hosted trial runs on Gemini. OpenRouter free stays as the hosted
FALLBACK** — its 50/day is real spillover capacity once Gemini's daily cap is
reached, and being a different vendor makes the failures uncorrelated (M0
measured 0% overlap between them).

Note this is a hosted-side fallback between two of *your* keys. It does not
weaken rule 1: a BYOK failure still never reaches any hosted key.

### 2.1 Trial needs its own API key, not just its own model

Trial and development would otherwise share one Gemini quota. M0/F6 established
quotas are per-model **per key**, so a demo day exhausts the capacity you need
to build with — and you find out mid-run.

Separate Google Cloud project, separate key, `TRIAL_GEMINI_API_KEY`. The
blast radius of trial abuse then stops at trial.

### 2.2 Trial identity must be cookie-first, not IP-first

Sitare students sit behind campus NAT. IP-based limits treat the entire
university as one user. Project 1 already hit exactly this and documented it:

> "Anonymous callers have no user id, so rate_limit_key falls back to client IP
> — and a whole campus behind one NAT is a single IP. 20/minute would have
> meant 20 questions per minute for the entire college."
> — `CollegeRag/backend/app/api/v1/chat.py`

So: a signed browser token is the primary identity, and IP is only a **coarse
secondary ceiling**, set high enough that a computer lab is not locked out.

**Neither is abuse-proof, and that is fine.** The control that actually bounds
cost is the global daily ceiling (§6). Per-identity limits shape the
distribution; only the global cap limits the bill.

---

## 3. Provider Manager

### 3.1 Three adapters, eight providers

The eight providers are three wire formats:

| Adapter | Serves |
|---|---|
| `openai_compatible.py` | OpenRouter, Groq, GitHub Models, OpenAI, Ollama, any custom endpoint |
| `gemini.py` | Google (exists today) |
| `anthropic.py` | Claude |

Everything else — base URL, default model, auth header style, capabilities — is
**catalogue data**.

### 3.2 The catalogue

```
provider_catalogue.json
└── openrouter
    ├── adapter: "openai_compatible"
    ├── base_url: "https://openrouter.ai/api/v1"
    ├── auth: { style: "bearer" }
    ├── models: [ { id, label, supports_tools, notes } ]
    ├── requires_key: true
    ├── allows_custom_base_url: false
    └── docs_url
```

**Adding Together AI, Fireworks, or DeepSeek = one catalogue entry.** Adding
Cohere's native API = a new adapter, because it is a genuinely different wire
format. That is the honest boundary: configuration covers new *vendors*, not
new *protocols*.

### 3.3 `supports_tools` is a required field

M0/E2 found models that accept a `tools` parameter and silently ignore it — the
agent then looks like it is refusing to act, with no error anywhere. The
catalogue records this per model, and a model without tool support is not
offered.

### 3.4 Resolution

```
resolve_provider(context) -> ResolvedProvider
```

```
1. context.byok is present?
       → build adapter from their config
       → NEVER falls through to step 2 on failure

2. context.mode == TRIAL and quota remains?
       → hosted key, pinned model, reduced budget

3. otherwise
       → NoProviderAvailable(reason)   # a typed reason, rendered by the UI
```

**Resolved once per run, not per call.** A run that switches provider mid-way
produces a trace where half the steps came from a different model — unreadable
when debugging, and it makes token accounting meaningless.

**Failover within a run** applies only to a user's own primary → their own
fallback, and writes a `provider_switch` step into the trace. An invisible
switch makes latency and token counts inexplicable.

```
ResolvedProvider
  ├── provider: LLMProvider     ← the existing Protocol, unchanged
  ├── mode: TRIAL | BYOK
  ├── budget: RunBudget         ← trial gets the reduced one
  └── label: "Gemini · gemini-3.1-flash-lite"    ← for the UI and the trace
```

`loop.py` receives `resolved.provider` and `resolved.budget`. It cannot tell
whose key paid. Same discipline as `knowledge.py` being the only file that
knows Project 1 exists.

### 3.5 What each adapter still owns

Not everything normalises away. M0 measured these differences and each adapter
must handle its own:

| Concern | Why it stays per-adapter |
|---|---|
| Schema dialect | Gemini takes an OpenAPI 3.0 subset, not JSON Schema |
| Error classification | M0/F2 — two 429s demanding opposite responses, worded differently by every vendor |
| Tool-call shape | OpenRouter returns arguments as a JSON **string**; Gemini as an **object** |
| Usage field names | `usage.prompt_tokens` vs `usageMetadata.promptTokenCount` |

---

## 4. Data model

### 4.1 New

```
trial_usage
  identity        TEXT      hash of the signed browser token
  day             DATE      UTC
  runs_used       INT
  tokens_used     BIGINT
  first_seen_at   TIMESTAMPTZ
  UNIQUE (identity, day)

trial_usage_ip           -- coarse secondary ceiling only
  ip_hash, day, runs_used
  UNIQUE (ip_hash, day)

global_usage             -- ONE row per day. The control that bounds cost.
  day, runs_used, tokens_used, estimated_cost
```

IPs are hashed. They are personal data, and the raw value is never needed —
only equality.

### 4.2 Changes to `runs`

```
+ mode            TEXT     'trial' | 'byok'
+ provider_name   TEXT     'gemini', 'openrouter', ...
+ model           TEXT
+ identity        TEXT     nullable
```

Enough to answer "what served this run" from the trace alone, with no new
instrumentation — the same principle the metrics already follow.

### 4.3 Where BYOK credentials live

**Server-side session store, never the `runs`/`steps` tables, never logs.**

A run makes ~12 provider calls over minutes, so the key must persist across
requests. "Session-only" therefore means *your server holds it for the session*
— say that plainly in the UI rather than implying it never leaves the browser.

| Storage | Verdict |
|---|---|
| Session store, TTL, encrypted at rest | **Default.** Matches "save for the current session". |
| Persisted per account, encrypted | Only once accounts exist and the user opts in |
| Browser localStorage, sent per request | Rejected — the key crosses the wire on every call and lands in more logs, not fewer |

---

## 5. Frontend

### 5.1 First open

```
            What should power your agent?

  ┌───────────────────────────┐   ┌───────────────────────────┐
  │  🚀  Try it now            │   │  🔑  Use my own key        │
  │                           │   │                           │
  │  No signup, no key.       │   │  Unlimited runs.          │
  │  3 runs per day.          │   │  Any model you like.      │
  │  Shorter reasoning.       │   │  You pay your provider.   │
  └───────────────────────────┘   └───────────────────────────┘

           Ollama runs locally and needs no key at all.
```

That last line matters: it is the genuinely free unlimited path, and burying it
sends people to trial mode who did not need to be there.

### 5.2 BYOK wizard

**Step 1 — provider.** Cards with honest labels, not a dropdown:

```
  Gemini        generous free tier, fast
  Groq          very fast, generous free tier
  OpenRouter    many models, free tier is ~50 requests/day
  Ollama        runs on your machine, no key needed
  OpenAI        paid
  Anthropic     paid
  GitHub Models free with a GitHub account
  Custom        any OpenAI-compatible endpoint
```

The OpenRouter note is M0/F5 stated plainly. A user who picks it expecting
unlimited use will hit 3–5 runs and conclude the app is broken.

**Step 2 — key.** Password field, provider-specific help link. Custom endpoints
additionally take a base URL, which is validated (§7.1).

**Step 3 — test.** One real call, reporting three things:

```
  ✓  Connected                    412 ms
  ✓  Tool calling supported
  ✓  Model: gemini-3.1-flash-lite
```

The middle check is the one that saves support tickets. A model without tool
calling produces an agent that appears to refuse every task.

**Step 4 — fallback (optional).** "Add a second provider for when the first is
rate-limited." Skippable.

### 5.3 Persistent status

```
Trial:  ⚡ Trial · 2 of 3 runs left today · [Use my own key]
BYOK:   🔑 Gemini · flash-lite · [Manage providers]
```

### 5.4 The two states that need real design

**Trial exhausted** — not an error. This is the conversion moment:

```
  You've used all 3 trial runs for today.

  Connect your own provider for unlimited runs — Gemini and
  Groq both have free tiers, and Ollama needs no key at all.

  [ Connect a provider ]        [ Come back tomorrow ]
```

**Their key fails mid-run** — the trace already reports this honestly, because
`unavailable` is a distinct state (MASTERGUIDE §7.4). Surface it as:

```
  ⚠ Your OpenRouter key hit its daily limit at step 4.

  OpenRouter's free tier allows about 50 requests per day, and one
  agent run uses 10–15.

  [ Switch to fallback ]   [ Update key ]   [ See partial answer ]
```

Never a silent switch to the hosted key. That is rule 1.

---

## 6. Quota

### 6.1 Layers, cheapest first

| Layer | Limit | Enforced |
|---|---|---|
| Per run | 6 steps / 40k tokens (trial) | `budget.py` — **already built** |
| Per identity / day | 3 runs | before the run starts |
| Per IP / day | 30 runs | coarse ceiling; generous for campus NAT |
| **Global / day** | **~120 runs** | **the control that bounds cost** |
| Vendor account | hard spend cap | the only limit a bug in your code cannot bypass |

**Where 120 comes from:** Gemini free supports ~150–180 trial runs/day (§2.0).
Setting the global ceiling below the vendor's own limit means you hit *your*
cap first — which fails with a message you wrote, not a 429 mid-run. Leave the
headroom; do not tune the ceiling up to the vendor limit.

On an entirely free stack there is no money at risk, so the "spend cap" is
really a *capacity* cap. The moment any paid key enters hosted mode, set a hard
spend limit in the vendor dashboard **before** shipping — it is the backstop
for the bug you have not written yet.

### 6.2 Fail closed

If the quota store is unreachable, **refuse trial runs**. Failing open is how a
bad deploy becomes a bill. BYOK runs are unaffected — they cost you nothing.

### 6.3 Reserve, then reconcile

Decrement the counter **before** the run, not after. A run that crashes at step
9 has already spent the tokens; refunding on failure lets a deliberately
crashing run loop forever.

### 6.4 What to watch

| Signal | Meaning |
|---|---|
| global runs/day vs ceiling | headroom |
| distinct identities/day | organic growth or automation |
| **tokens per run, p95** | a jump means someone found how to make runs expensive |
| trial → BYOK conversion | whether trial is doing its job |

---

## 7. Security

### 7.1 Custom base URL is SSRF ⚠️

The sharpest risk in this design and one it introduces. "OpenAI-compatible
endpoint" means the user supplies a URL and the server POSTs to it. Point it at
`http://169.254.169.254/…` and the app becomes an SSRF proxy into its own
infrastructure.

**The validator already exists.** `web_read.py` resolves every hostname and
rejects non-public addresses, re-checking after each redirect. The same
function guards provider base URLs.

**Ollama is the deliberate exception.** It is *meant* to be `localhost:11434`.
That needs an explicit, separately-labelled allowance — a named exception for a
loopback Ollama URL, not a hole in the check. And it is only ever safe because
Ollama runs on the *user's* machine: in a hosted deployment there is no user
machine, so the exception must be off by default in production.

### 7.2 Keys leaking into the trace

`steps` stores tool inputs, outputs and errors, and provider errors echo
request context freely. Redaction runs **on write**, not on display — the
display path is not the one that gets exported, dumped, or shipped to a log
aggregator.

Belt and braces: a test that runs a real BYOK request with a marker key and
asserts the marker appears nowhere in the database.

### 7.3 Trial abuse

Cookies clear; IPs rotate. Assume roughly 10× the intended per-identity limit
leaks through. This is precisely why the trial allowance is small — abuse of 3
runs is boring — and why the global ceiling is the real control.

Cheap friction worth adding if abuse appears: a signed token required to start
a run, issued on page load; proof-of-work on the first run of a day.

### 7.4 Provider config must be unreachable by tools

Currently contained because every tool is read-only. If a future tool could
read or change provider settings, prompt injection becomes key theft. Provider
configuration is not, and must never become, tool-accessible state.

### 7.5 Model selection is validated, not free text

A user typing an arbitrary model id can select one without tool support, and the
agent then fails in a way that looks like a bug in the app. Selection comes from
the catalogue; custom ids are allowed but must pass the tool-calling check
before being saved.

---

## 8. Milestones

Following the format in `DEVELOPMENT_STRATEGY.md`. Each leaves the system
working, and you can stop after any of them.

### P1 — Provider Manager (no users, no UI)
- **Goal**: three adapters, catalogue as data, `resolve_provider()`, base-URL SSRF validation, redaction on write. BYOK via env var.
- **Why first**: valuable alone — provider switching becomes configuration instead of code, which M0 showed is needed. The agent loop does not change.
- **DoD**: `cli.py run --provider groq` works; `loop.py` has no provider-specific branch; a marker key appears nowhere in the database.

### P2 — Runs API *(this is M32)*
- **Goal**: `POST /runs` returning 202, SSE trace streaming, signed browser identity.
- **Why**: trial mode needs a request boundary and an identity to count against.
- **DoD**: a browser can start a run and watch the trace; identity survives a reload.

### P3 — Trial mode
- **Goal**: quota tables, layered enforcement, fail-closed, reduced trial budget, separate trial key.
- **DoD**: the 4th run in a day is refused with a message, not an error. Vendor spend cap set **before** this ships.

### P4 — BYOK UI *(this is M45)*
- **Goal**: onboarding choice, provider wizard, connection test, status bar, exhaustion screen.
- **DoD**: a new user reaches a completed run without reading documentation.

### P5 — Accounts
- **Goal**: signup, persisted encrypted provider config, per-account quota.
- **Trigger**: only if P3 shows people actually hitting the trial wall. Building it before that is speculation.

---

## 9. Open questions

1. **Does trial mode get the full tool set?** `web_search` costs Tavily quota too (1,000/month free). At 120 trial runs/day that is exhausted in under a week. Trial is probably `knowledge_search` + `calculator` only — which is also the honest demo, since those two are what make the agent visibly different from a chatbot.
2. **Ollama in a hosted deployment** — the loopback exception only makes sense when the user runs the app locally. Two builds, or off in production?
3. **Do trial runs get retained?** They are the best onboarding-funnel data and also the most disposable. A retention window should be a decision, not a default.
4. **When Gemini's daily cap hits, does OpenRouter spillover actually help?** It adds ~6 runs. Worth wiring only because the code path (hosted fallback) is the same one that makes the system resilient to a Gemini outage.

---

## 10. Summary of what changed from the original plan

| Original | Revised | Why |
|---|---|---|
| Hosted on OpenRouter free | **Hosted on Gemini**, OpenRouter as spillover | 6 runs/day vs ~150. M0/F5. |
| One hosted key | **Separate trial key/project** | Trial abuse must not exhaust development quota. M0/F6. |
| IP-based trial limits | **Cookie-first, IP as coarse ceiling** | Campus NAT — P1 documented this exact problem |
| 9 providers | **8 providers, 3 adapters** | They are three wire formats, not nine |
| Per-user limits as cost control | **Global ceiling as cost control** | Per-identity limits shape distribution; only the global cap bounds it |
| Model as free text | **Validated against catalogue** | M0/E2 — models that accept `tools` and ignore them |
