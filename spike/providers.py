"""Raw provider calls for the M0 spike. DELIBERATELY DUPLICATED.

There is no LLMProvider abstraction in this file and there must not be one.
ARCHITECTURE.md §A5 proposes an interface; that interface is a *hypothesis*.
This spike exists to test it. Writing the abstraction first would mean
designing around differences we have not observed yet, and we would
unconsciously shape the experiments to fit the interface we already wrote.

So: two ugly functions that repeat themselves. Let the duplication be
obvious. Every place they differ is a requirement for the real `Completion`
type in M6, and the refactor then becomes a summary of evidence rather than
a guess.

This whole directory is deleted at M6. Only two things survive:
  - results/*.jsonl  (the raw-response corpus -> M10 test fixtures)
  - PROVIDER_EVALUATION.md (the findings)
Do not polish this code.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
load_dotenv(HERE / ".env")


# ---------------------------------------------------------------------------
# Config. Plain os.getenv, no pydantic-settings — the app gets that at M2;
# a 20-line spike does not need a settings class.
# ---------------------------------------------------------------------------

def _csv(name: str) -> list[str]:
    return [v.strip() for v in os.getenv(name, "").split(",") if v.strip()]


OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODELS = _csv("OPENROUTER_MODELS")
OPENROUTER_PIN = os.getenv("OPENROUTER_PIN_PROVIDER", "").strip()
OPENROUTER_ALLOW_FALLBACKS = os.getenv("OPENROUTER_ALLOW_FALLBACKS", "false").lower() == "true"

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODELS = _csv("GEMINI_MODELS")

TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "90"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "512"))
SLEEP = float(os.getenv("SLEEP_BETWEEN_CALLS", "4.0"))


# ---------------------------------------------------------------------------
# Recording. Append-only JSONL, one record per API call.
#
# Same reasoning as the `steps` table in the real design (ARCHITECTURE.md §B2):
# append-only means collection and analysis are separate concerns. We can add a
# new failure class to the classifier and re-score the entire corpus without
# spending another token of quota. Collect once, analyse many times.
# ---------------------------------------------------------------------------

def record(experiment: str, payload: dict) -> None:
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"{experiment}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------------------
# OpenRouter  (OpenAI-compatible Chat Completions)
# ---------------------------------------------------------------------------

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def call_openrouter(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: str | None = None,
    temperature: float = 0.0,
) -> dict:
    """One raw OpenRouter call. Returns a transport-level envelope, NOT a
    normalised completion — normalising here would be the abstraction we are
    deliberately not building yet.

    `tools` is OpenAI format:
        [{"type": "function",
          "function": {"name": ..., "description": ..., "parameters": <JSON Schema>}}]
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
        # Optional attribution headers. Harmless, and OpenRouter's docs ask
        # for them; recorded here so the request we send is fully visible.
        "HTTP-Referer": os.getenv("OPENROUTER_APP_URL", ""),
        "X-Title": os.getenv("OPENROUTER_APP_TITLE", ""),
    }

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": MAX_TOKENS,
    }
    if tools:
        body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice

    # Provider pinning. OpenRouter routes one model NAME across several upstream
    # hosts running different hardware and quantisations (fp16 / int8 / int4).
    # Unpinned, two identical requests can hit two different machines, so any
    # reliability number we measure is a property of the routing lottery rather
    # than of the model. Pin it, or the results are not reproducible next week.
    if OPENROUTER_PIN or not OPENROUTER_ALLOW_FALLBACKS:
        provider: dict[str, Any] = {"allow_fallbacks": OPENROUTER_ALLOW_FALLBACKS}
        if OPENROUTER_PIN:
            provider["order"] = [OPENROUTER_PIN]
        body["provider"] = provider

    started = time.perf_counter()
    try:
        response = httpx.post(OPENROUTER_URL, headers=headers, json=body, timeout=TIMEOUT)
        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            raw = response.json()
        except ValueError:
            raw = {"_unparseable_body": response.text[:4000]}
        return {
            "provider": "openrouter",
            "model": model,
            "ok": response.status_code == 200,
            "status": response.status_code,
            "latency_ms": latency_ms,
            "raw": raw,
            "request": body,
            # Rate-limit headers are capacity-planning data for M19's budget.
            # Free tiers rarely document real limits; the headers tell the truth.
            "rate_headers": {
                k: v for k, v in response.headers.items()
                if "ratelimit" in k.lower() or "retry-after" in k.lower()
            },
            "error": None,
        }
    except Exception as e:
        return {
            "provider": "openrouter",
            "model": model,
            "ok": False,
            "status": None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "raw": None,
            "request": body,
            "rate_headers": {},
            "error": f"{type(e).__name__}: {e}",
        }


def peek_openrouter(raw: dict) -> dict:
    """Pull out the three things we care about, in OpenRouter's shape.

    GOTCHA worth internalising: `function.arguments` is a JSON *string*, not a
    JSON object. Gemini returns a real object (see peek_gemini). That single
    difference is the clearest proof that a normalising layer has to exist —
    and it is the kind of thing you only find by looking at real responses.
    """
    if not isinstance(raw, dict):
        return {"text": None, "tool_calls": [], "usage": {}, "finish_reason": None}

    choice = (raw.get("choices") or [{}])[0]
    message = choice.get("message") or {}

    tool_calls = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        args_raw = fn.get("arguments")
        parsed, parse_error = None, None
        if isinstance(args_raw, str):
            try:
                parsed = json.loads(args_raw)
            except json.JSONDecodeError as e:
                parse_error = str(e)
        elif isinstance(args_raw, dict):
            # Some upstreams return an object despite the spec. Record it —
            # it means the parser cannot assume either shape.
            parsed, parse_error = args_raw, "NON_SPEC_OBJECT_ARGS"
        tool_calls.append({
            "name": fn.get("name"),
            "arguments_raw": args_raw,
            "arguments": parsed,
            "parse_error": parse_error,
        })

    usage = raw.get("usage") or {}
    return {
        "text": message.get("content"),
        "tool_calls": tool_calls,
        "usage": {
            "prompt": usage.get("prompt_tokens"),
            "completion": usage.get("completion_tokens"),
            "total": usage.get("total_tokens"),
        },
        "finish_reason": choice.get("finish_reason"),
    }


# ---------------------------------------------------------------------------
# Gemini  (generateContent)
# ---------------------------------------------------------------------------

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def to_gemini_schema(schema: dict, uppercase_types: bool = False) -> dict:
    """Translate a JSON Schema into what Gemini accepts.

    Gemini does NOT take JSON Schema. It takes a subset of OpenAPI 3.0, which
    means several things Pydantic emits by default are invalid here:

      - $ref / $defs      : nested models become references -> must be inlined
      - additionalProperties, $schema, title, examples : not part of the subset
      - type casing       : the REST enum is upper-case ("STRING"), though
                            lower-case is often tolerated. WHICH ONE WORKS IS
                            AN E6 FINDING -- test both, don't guess.

    This is exactly the "schema dialect" problem from the M0 theory notes, and
    it is why the M6 provider interface must translate schemas, not just move
    HTTP bytes around.
    """
    drop = {"$schema", "$defs", "$ref", "additionalProperties", "title", "examples", "default"}

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k in drop:
                    continue
                if k == "type" and uppercase_types and isinstance(v, str):
                    out[k] = v.upper()
                else:
                    out[k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def call_gemini(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float = 0.0,
    uppercase_types: bool = False,
) -> dict:
    """One raw Gemini call.

    `messages` uses the SAME OpenAI-ish shape as call_openrouter so the
    experiment harness can feed both the identical case. Converting it to
    Gemini's shape happens here, and the conversion is itself a finding:

      role "assistant"  -> "model"
      role "system"     -> NOT a message at all; a separate systemInstruction
      content: str      -> parts: [{"text": ...}]

    `tools` is passed in OpenAI format and converted, so both providers are
    driven from one fixture file.
    """
    system_text = "\n".join(m["content"] for m in messages if m.get("role") == "system")

    contents = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        contents.append({
            "role": "model" if role == "assistant" else "user",
            "parts": [{"text": m.get("content") or ""}],
        })

    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": MAX_TOKENS},
    }
    if system_text:
        body["systemInstruction"] = {"parts": [{"text": system_text}]}

    if tools:
        declarations = []
        for t in tools:
            fn = t.get("function", t)
            declarations.append({
                "name": fn["name"],
                "description": fn["description"],
                "parameters": to_gemini_schema(fn["parameters"], uppercase_types),
            })
        body["tools"] = [{"functionDeclarations": declarations}]

    started = time.perf_counter()
    try:
        response = httpx.post(
            GEMINI_URL.format(model=model),
            headers={"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"},
            json=body,
            timeout=TIMEOUT,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            raw = response.json()
        except ValueError:
            raw = {"_unparseable_body": response.text[:4000]}
        return {
            "provider": "gemini",
            "model": model,
            "ok": response.status_code == 200,
            "status": response.status_code,
            "latency_ms": latency_ms,
            "raw": raw,
            "request": body,
            "rate_headers": {
                k: v for k, v in response.headers.items()
                if "ratelimit" in k.lower() or "retry-after" in k.lower()
            },
            "error": None,
        }
    except Exception as e:
        return {
            "provider": "gemini",
            "model": model,
            "ok": False,
            "status": None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "raw": None,
            "request": body,
            "rate_headers": {},
            "error": f"{type(e).__name__}: {e}",
        }


def peek_gemini(raw: dict) -> dict:
    """Same three things, in Gemini's completely different shape.

    Read this next to peek_openrouter. Every structural difference between the
    two is a requirement for M6:

      choices[0].message          vs  candidates[0].content
      .content (str)              vs  .parts[] (list, text AND calls mixed in)
      .tool_calls[].function      vs  .parts[].functionCall
      arguments: JSON STRING      vs  args: real JSON OBJECT   <- the big one
      usage.prompt_tokens         vs  usageMetadata.promptTokenCount
      finish_reason: "stop"       vs  finishReason: "STOP"
    """
    if not isinstance(raw, dict):
        return {"text": None, "tool_calls": [], "usage": {}, "finish_reason": None}

    candidate = (raw.get("candidates") or [{}])[0]
    parts = ((candidate.get("content") or {}).get("parts")) or []

    texts, tool_calls = [], []
    for part in parts:
        if "text" in part:
            texts.append(part["text"])
        if "functionCall" in part:
            fc = part["functionCall"]
            tool_calls.append({
                "name": fc.get("name"),
                # Already an object. No json.loads, so no parse failure is even
                # possible on this path — a genuine reliability advantage.
                "arguments_raw": fc.get("args"),
                "arguments": fc.get("args"),
                "parse_error": None,
            })

    usage = raw.get("usageMetadata") or {}
    return {
        "text": "\n".join(texts) if texts else None,
        "tool_calls": tool_calls,
        "usage": {
            "prompt": usage.get("promptTokenCount"),
            "completion": usage.get("candidatesTokenCount"),
            "total": usage.get("totalTokenCount"),
        },
        "finish_reason": candidate.get("finishReason"),
    }


def peek(envelope: dict) -> dict:
    """Dispatch to the right peek_*. This tiny function is the ONLY concession
    to uniformity in the file, and it exists solely so the harness can loop.
    It is not the abstraction — it hides nothing and normalises nothing beyond
    field names we have already proven differ."""
    fn = peek_openrouter if envelope["provider"] == "openrouter" else peek_gemini
    return fn(envelope.get("raw") or {})


# ---------------------------------------------------------------------------
# E1 smoke test:  python providers.py
# Proves credentials work and shows the raw response shapes side by side.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from rich.console import Console
    from rich.table import Table

    console = Console()
    messages = [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "Reply with exactly the word: pong"},
    ]

    targets = [("openrouter", m) for m in OPENROUTER_MODELS] + \
              [("gemini", m) for m in GEMINI_MODELS]

    if not targets:
        console.print("[red]No models configured.[/] Fill OPENROUTER_MODELS / GEMINI_MODELS in .env")
        raise SystemExit(1)

    table = Table(title="E1 — connectivity, latency, usage")
    for col in ("provider", "model", "status", "ms", "prompt tok", "compl tok", "text"):
        table.add_column(col, overflow="fold")

    for provider, model in targets:
        caller = call_openrouter if provider == "openrouter" else call_gemini
        env = caller(model, messages)
        view = peek(env)
        record("e1_connectivity", {**env, "peek": view})

        table.add_row(
            provider,
            model,
            str(env["status"]) if env["ok"] else f"[red]{env['status'] or 'ERR'}[/]",
            str(env["latency_ms"]),
            str(view["usage"].get("prompt")),
            str(view["usage"].get("completion")),
            (view["text"] or env["error"] or "")[:60],
        )
        if env["rate_headers"]:
            console.print(f"[dim]{provider}/{model} rate headers: {env['rate_headers']}[/]")
        time.sleep(SLEEP)

    console.print(table)
    console.print(f"[dim]Raw responses appended to {RESULTS / 'e1_connectivity.jsonl'}[/]")
