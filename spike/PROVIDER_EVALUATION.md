# M0 — Provider Evaluation

**Status: COMPLETE. Provider decision FROZEN.**
Run 2026-07-23. 180 scored calls (E3/E4) plus E1, E2, E5, E6.

This file and `results/*.jsonl` are the only artifacts of M0 that survive; the
spike code is deleted at M6.

---

## THE DECISION

| Role | Model | Why |
|---|---|---|
| **Primary** | `gemini-3.1-flash-lite` | 100% format over a full 36/36 sample; never hit a quota wall; **only model to clear the E5 hard gate** |
| **Same-vendor secondary** | `gemini-2.5-flash` | 100% format, 100% selection, lowest token cost (324 avg/call) — but on 13 calls, and quota-thin |
| **Cross-vendor fallback** (deferred to M11) | `openai/gpt-oss-20b:free` | 97.2% format, 91.4% selection, **0% failure overlap** with the primary |

### Amendment 1 — 2026-07-23, after M8

**`gemini-3.1-flash-lite` promoted over `gemini-2.5-flash`.** The original
freeze ranked on quality alone. The first live call after M8 shipped returned
429: the spike's own 36 calls had exhausted `gemini-2.5-flash`'s daily quota,
while `gemini-3.1-flash-lite` and `gemini-flash-lite-latest` both answered
correctly (`knowledge_search({"query": "minimum CGPA to keep scholarship"})`,
~1.0 s, 159+21 tokens).

Three reasons the reversal is right, not expedient:

1. **Availability is a capability.** A model that cannot be called is worth
   less than one with a lower selection score. `gemini-2.5-flash` was
   unreachable within hours of being frozen.
2. **Sample size.** The 100/100 rested on 13 scored calls — a wide error bar.
   flash-lite's 100% format came from a full 36/36 with zero quota failures.
3. **flash-lite is the only model with a verified Gate 2 pass.** Multi-turn is
   the hard gate, and it is the sole model that cleared it.

Its 75% selection score is the one genuine concern — and M0/F7 attributes
almost all of it to the `knowledge_list_documents` description bug, which M12
fixes. **Re-measure selection after M12 before treating 75% as this model's
real number.**

**Architecture change: ARCHITECTURE.md §C2 is INVERTED.** It named OpenRouter
primary and Gemini fallback. The evidence reverses it. See F5.

**Gate 2 (multi-turn) is only PARTIALLY verified** — see "Outstanding" below.
This is a real caveat, not a formality.

---

## Results

### Gates 1 & 3 — capability

Format compliance is scored **only over calls that reached the model**. A 429
never got there; counting it as a format failure conflates *availability* (a
billing property) with *capability* (whether the model can emit a parseable
tool call). Two different problems, two different fixes.

| Model | n | reached | format % | selection % | p50 ms | avg tok | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| `gemini-2.5-flash` | 36 | 13 (36%) | **100.0** | **100.0** | 1069 | **324** | PASS |
| `gemini-3.5-flash` | 36 | 20 (56%) | **100.0** | 90.0 | 1048 | 530 | PASS |
| `gemini-3.1-flash-lite` | 36 | 36 (100%) | **100.0** | 75.0 | 778 | 960 | weak selection |
| `openai/gpt-oss-20b:free` | 36 | 36 (100%) | 97.2 | 91.4 | **6145** | 945 | PASS |
| `nvidia/nemotron-3-super-120b:free` | 36 | 17 (47%) | 88.2 | 93.3 | 487 | 622 | needs repair loop |

### Gate 2 — multi-turn (HARD GATE)

| Model | Result |
|---|---|
| `gemini-3.1-flash-lite` | **PASS** — `knowledge_search` → `calculator`, the correct trajectory |
| all others | **untested** — quota exhausted before they could run |

### Gate 4 — failure-set overlap

Threshold: <70%. Measured across all ten model pairs: **0–20%**. Passes
comfortably. `gemini-2.5-flash` vs `gpt-oss-20b` is **0%** — a genuinely
uncorrelated fallback.

### E6 — schema dialect

`gemini-3.1-flash-lite` accepted the full probe schema — required string,
optional int, **enum, array, and nested object** — and returned all five
fields. After `to_gemini_schema()` strips `$ref`/`$defs`/`additionalProperties`,
nested schemas survive.

---

## Findings

### F1 — `google/*:free` on OpenRouter is Gemini with an extra hop
Its 429 named `provider_name: "Google AI Studio"`. OpenRouter proxies Google's
own API rather than hosting these models, so it shares an upstream, a quota
pool, and an outage surface with direct Gemini calls. Fails Gate 4 by
construction. **An aggregator's model list is not a list of independent
providers.** Dropped.

### F2 — Two 429s that demand opposite responses
```
gemma-4-31b : "temporarily rate-limited upstream"   -> transient, back off
gemini-2.0  : "limit: 0, free_tier_requests"        -> PERMANENT, no free tier
```
Identical status code. **M11 consequence:** the router cannot decide failover
from status codes alone. Body parsing is load-bearing, not defensive.

### F3 — 10× prompt-token spread on identical input
8 tokens (Gemini) vs 28 (Nemotron) vs 85 (`gpt-oss-20b`) for the same 2-message
prompt — chat-template overhead. `gpt-oss-20b` additionally spent 76 completion
tokens to emit the word "pong": it is a reasoning model and emits reasoning
tokens unconditionally.

### F4 — P1's model choice does not transfer to P2
Project 1 uses `gpt-oss-20b:free` correctly: one call per question, latency
hidden behind a typing indicator. P2 makes 10–15 calls per run, where the same
model's **p50 of 6145 ms** becomes ~75 s of pure model time per run.
**A model can be right for RAG and wrong for an agent.**

### F5 — OpenRouter's free tier cannot host an agent. This decides the architecture.
Hit empirically, with the cap stated in the error body:
```
Rate limit exceeded: free-models-per-day.
Add 10 credits to unlock 1000 free model requests per day
```
An agent run is 10–15 LLM calls. At the uncredited free cap that is roughly
**3–5 agent runs per day** — not a development environment, let alone
production. A single golden-set evaluation (30 goals × ~10 steps ≈ 300 calls)
is impossible.

This is why the primary/fallback ordering inverts. It is not a quality
judgement — `gpt-oss-20b` scored 97.2% format / 91.4% selection and is a fine
model. It is a **capacity** judgement.

**Two ways out, both cheap:** $10 of OpenRouter credits unlocks 1000/day, or
spread load across several Gemini models, whose quotas are **per-model** (F6).

### F6 — Gemini quotas are per-model, which is exploitable
When `gemini-2.5-flash` and `gemini-3.5-flash` were exhausted,
`gemini-3.1-flash-lite` kept serving. Distinct quota pools.
**M11 consequence:** the router's fallback chain should include *sibling models
at the same vendor*, not only a different vendor. Cheapest capacity available.

### F7 — Every selection failure is one tool-description bug
All 15 wrong choices across all models point the same way:

| Goal | Wanted | Got instead |
|---|---|---|
| G01, G03, G05, G10 | `knowledge_search` | `knowledge_list_documents` ×10 |
| G09 | `final_answer` | `knowledge_list_documents` ×2 |
| G02 | `knowledge_list_documents` | `knowledge_search` ×3 |

The culprit is one word. `knowledge_list_documents`'s description opens
*"Use this **FIRST** when you need to know what documents exist"* — and models
read "FIRST" as "before anything else", so it wins on unrelated goals. G02 is
the mirror image: the genuine whole-document task went to `knowledge_search`.

**This is a tool-design defect, not a model weakness** — which is exactly the
distinction M0 was built to expose. `gemini-3.1-flash-lite`'s 75% selection
score is almost entirely this one bug.

**M12/M16 consequence:** remove "FIRST" from the description; state the
*trigger condition* instead ("when you need an inventory of what exists, or a
document_id to read"). Re-measure before concluding anything about model
selection quality. **Expected to move flash-lite from 75% toward 95%+.**

### F8 — Schema dialect caution was warranted, flat-args caution was not
`to_gemini_schema()` (stripping `$ref`, `$defs`, `additionalProperties`) is
required. But once translated, enums, arrays, and nested objects all survive.
**M12 consequence:** tool args need not be restricted to flat scalars. The
translation layer is mandatory; the flat-args restriction is not.

### F9 — A harness that cannot tell "wrong" from "unavailable" produces confident lies
E5's first version reported five 429s as five model failures — it checked
`if not tool_calls` before checking `if not response.ok`. Read literally, it
said four of five models cannot sustain a multi-turn tool loop. All five were
simply throttled.

**Carried into M13:** a tool executor must classify *unavailable* separately
from *failed*, because the agent's correct response differs — retry later
versus replan without the tool.

---

## Outstanding — must close before M23

1. **Gate 2 verified on only 1 of 5 models.** Re-run `python run.py e5` after
   quota resets. **`gemini-2.5-flash` must pass before it is trusted as
   primary.** If it fails, `gemini-3.1-flash-lite` (which passed) is promoted.
2. **`gemini-2.5-flash`'s 100/100 rests on 13 scored calls.** Small sample with
   a wide error bar. Re-run E3 restricted to it (`--only gemini-2.5-flash`) for
   36 clean calls before treating the number as settled.
3. **E7 (repair-loop efficacy) not run** — only 3 non-quota format failures
   occurred in 180 calls, too few to measure a recovery rate. That is itself
   the finding: **format failure is rare enough on these models that the repair
   loop is a safety net, not a load-bearing component.** M10 is downgraded from
   "may become primary" to "fallback only".

---

## Downstream decisions, resolved

| Milestone | Resolved by M0 |
|---|---|
| M6 | `Completion` must carry both OpenRouter's `arguments` (JSON **string**) and Gemini's `args` (JSON **object**) |
| M7/M8 | Both providers confirmed working; Gemini needs schema translation, OpenRouter needs provider pinning |
| M9 | **Native tool calling for all 5 models.** No capability probing needed — config declares it |
| M10 | **Fallback only, not primary.** 3 format failures in 180 calls (F7/E7) |
| M11 | Parse error *bodies*, not just status codes (F2). Fallback chain includes **sibling models at the same vendor** (F6) |
| M12 | Nested schemas are fine (F8). **Rewrite `knowledge_list_documents`'s description** (F7) |
| M13 | Classify *unavailable* separately from *failed* (F9) |
| M19 | Budget: p50 ~1.0 s and ~324–960 tokens/call on Gemini. A 15-step run ≈ 15 s model time, ≈5–15k tokens. `MAX_STEPS=15` is affordable. On `gpt-oss-20b` the same run is ~90 s |
| M20 | Native `tools` parameter, not prompted JSON or ReAct text |
| M21 | `MULTI_CALL` never observed — one tool per turn is safe |
| M23 | Multi-turn works via a plain assistant/user exchange; native `role:"tool"` messages are not required for portability |
| M39 | The 12 goals in `fixtures.py` graduate to the golden set. G01, G02, G03, G09, G10 are the discriminating cases |
