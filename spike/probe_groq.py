"""P0 — measure Groq the same way M0 measured the other five models.

"Groq has a generous free tier" is a claim, not a number. Before it becomes the
hosted default (or is dismissed), it gets the same treatment as everything else:
E1 connectivity, E2 native tool-call support, E3/E4 format + selection over the
12 labelled goals, E5 the multi-turn hard gate.

Groq speaks the OpenAI Chat Completions format, so the call is shaped exactly
like the OpenRouter one in providers.py.

    python probe_groq.py models     list what the key can reach
    python probe_groq.py            run E1, E2, E3/E4, E5
"""

from __future__ import annotations

import os
import sys
import time

import httpx
from dotenv import load_dotenv
from rich.console import Console

from classify import OK, classify
from fixtures import GOALS, SYSTEM_PROMPT, TOOLS
from providers import HERE, record

load_dotenv(HERE / ".env")
console = Console(emoji=False)

GROQ_KEY = os.getenv("GROQ_API_KEY", "")
BASE = "https://api.groq.com/openai/v1"
SLEEP = float(os.getenv("SLEEP_BETWEEN_CALLS", "3.0"))
TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "90"))

_client = httpx.Client(timeout=TIMEOUT)


def call_groq(model: str, messages: list[dict], tools=None, temperature: float = 0.0) -> dict:
    """One raw Groq call. Same envelope as providers.call_openrouter so the
    same classifier and the same peek logic apply unchanged."""
    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": int(os.getenv("MAX_TOKENS", "512")),
    }
    if tools:
        body["tools"] = tools

    started = time.perf_counter()
    try:
        r = _client.post(
            f"{BASE}/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json=body,
        )
        latency = int((time.perf_counter() - started) * 1000)
        try:
            raw = r.json()
        except ValueError:
            raw = {"_unparseable_body": r.text[:3000]}
        return {
            "provider": "groq", "model": model, "ok": r.status_code == 200,
            "status": r.status_code, "latency_ms": latency, "raw": raw, "request": body,
            # Groq documents its remaining-quota headers; they answer the
            # "generous free tier" question directly rather than by inference.
            "rate_headers": {k: v for k, v in r.headers.items()
                             if "ratelimit" in k.lower() or "retry-after" in k.lower()},
            "error": None,
        }
    except Exception as e:
        return {
            "provider": "groq", "model": model, "ok": False, "status": None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "raw": None, "request": body, "rate_headers": {},
            "error": f"{type(e).__name__}: {e}",
        }


def peek_groq(raw: dict) -> dict:
    """OpenAI shape: arguments arrive as a JSON STRING and must be parsed —
    the failure class that is structurally impossible on Gemini's path."""
    import json

    if not isinstance(raw, dict):
        return {"text": None, "tool_calls": [], "usage": {}, "finish_reason": None}

    choice = (raw.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    calls = []
    for c in msg.get("tool_calls") or []:
        fn = c.get("function") or {}
        args_raw = fn.get("arguments")
        parsed, err = None, None
        if isinstance(args_raw, str):
            try:
                parsed = json.loads(args_raw)
            except json.JSONDecodeError as e:
                err = str(e)
        elif isinstance(args_raw, dict):
            parsed, err = args_raw, "NON_SPEC_OBJECT_ARGS"
        calls.append({"name": fn.get("name"), "arguments_raw": args_raw,
                      "arguments": parsed, "parse_error": err})

    u = raw.get("usage") or {}
    return {
        "text": msg.get("content"),
        "tool_calls": calls,
        "usage": {"prompt": u.get("prompt_tokens"), "completion": u.get("completion_tokens"),
                  "total": u.get("total_tokens")},
        "finish_reason": choice.get("finish_reason"),
    }


def msgs(goal: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": goal}]


def list_models() -> list[str]:
    r = _client.get(f"{BASE}/models", headers={"Authorization": f"Bearer {GROQ_KEY}"})
    if r.status_code != 200:
        console.print(f"[red]{r.status_code}[/] {r.text[:300]}")
        return []
    ids = sorted(m["id"] for m in r.json().get("data", []))
    for i in ids:
        console.print(f"  {i}")
    return ids


def main(models: list[str], trials: int = 2) -> None:
    console.print(f"[bold]P0 — Groq probe[/]  models={models}  trials={trials}\n")

    # --- E1 + E2 -----------------------------------------------------------
    console.print("[bold]E1/E2[/] connectivity and native tool-call support")
    alive = []
    for model in models:
        env = call_groq(model, msgs(GOALS[0]["goal"]), tools=TOOLS)
        view = peek_groq(env.get("raw") or {})
        record("p0_groq_e2", {**env, "peek": view})

        if not env["ok"]:
            err = ((env.get("raw") or {}).get("error") or {}).get("message") or env.get("error")
            console.print(f"  {model:38s} [red]{env['status']}[/] {str(err)[:70]}")
            continue
        if view["tool_calls"]:
            console.print(
                f"  {model:38s} [green]NATIVE[/] -> {[c['name'] for c in view['tool_calls']]}"
                f"  {env['latency_ms']}ms  {view['usage'].get('prompt')}+{view['usage'].get('completion')}tok"
            )
            alive.append(model)
        else:
            console.print(f"  {model:38s} [yellow]TEXT-ONLY (tools ignored)[/]")
        if env["rate_headers"]:
            console.print(f"    [dim]{env['rate_headers']}[/]")
        time.sleep(SLEEP)

    if not alive:
        console.print("\n[red]No Groq model emitted a tool call. Nothing further to measure.[/]")
        return

    # --- E3 / E4 -----------------------------------------------------------
    console.print(f"\n[bold]E3/E4[/] format compliance + selection ({len(GOALS)} goals x {trials})")
    for model in alive:
        counts: dict[str, int] = {}
        right = scored = 0
        for goal in GOALS:
            for trial in range(trials):
                env = call_groq(model, msgs(goal["goal"]), tools=TOOLS)
                view = peek_groq(env.get("raw") or {})
                res = classify(env, view, goal)
                record("p0_groq_e3", {**env, "peek": view, "goal_id": goal["id"],
                                      "trial": trial, **res})
                counts[res["format_class"]] = counts.get(res["format_class"], 0) + 1
                if res["selection_ok"] is not None:
                    scored += 1
                    right += int(res["selection_ok"])
                console.print("." if res["format_class"] == OK else "x", end="")
                time.sleep(SLEEP)

        reached = sum(v for k, v in counts.items() if k != "API_ERROR")
        fmt = 100 * counts.get(OK, 0) / reached if reached else 0
        sel = 100 * right / scored if scored else 0
        console.print(
            f"\n  [bold]{model}[/]  reached {reached}/{sum(counts.values())}"
            f"  format {fmt:.1f}%  selection {sel:.1f}%  {counts}\n"
        )

    # --- E5 hard gate ------------------------------------------------------
    console.print("[bold]E5[/] multi-turn (HARD GATE)")
    goal = next(g for g in GOALS if g["id"] == "G12")
    tool_result = (
        "knowledge_search returned 1 passage:\n"
        '[1] (document 3, page 2, score 0.81) "Applicants must appear for JEE Mains 2026; '
        'a percentile of 85 or above is generally expected."'
    )
    for model in alive:
        env1 = call_groq(model, msgs(goal["goal"]), tools=TOOLS)
        v1 = peek_groq(env1.get("raw") or {})
        record("p0_groq_e5", {**env1, "peek": v1, "turn": 1})

        if not env1["ok"]:
            console.print(f"  {model:38s} [yellow]SKIP[/] {env1['status']}")
            continue
        if not v1["tool_calls"]:
            console.print(f"  {model:38s} [red]FAIL turn 1[/] no tool call")
            continue

        first = v1["tool_calls"][0]["name"]
        convo = msgs(goal["goal"]) + [
            {"role": "assistant", "content": f"I called {first}."},
            {"role": "user", "content": f"Tool result:\n{tool_result}\n\nContinue. Take your next action."},
        ]
        env2 = call_groq(model, convo, tools=TOOLS)
        v2 = peek_groq(env2.get("raw") or {})
        record("p0_groq_e5", {**env2, "peek": v2, "turn": 2})

        if not env2["ok"]:
            verdict = f"[yellow]SKIP turn 2[/] {env2['status']}"
        elif not v2["tool_calls"]:
            verdict = f"[red]FAIL turn 2[/] {(v2['text'] or '')[:60]!r}"
        else:
            second = v2["tool_calls"][0]["name"]
            if second == first:
                verdict = f"[red]FAIL[/] repeated {second} (stuck)"
            elif second in {"calculator", "final_answer"}:
                verdict = f"[green]PASS[/] {first} -> {second}"
            else:
                verdict = f"[yellow]WEAK[/] {first} -> {second}"
        console.print(f"  {model:38s} {verdict}")
        time.sleep(SLEEP)


if __name__ == "__main__":
    if not GROQ_KEY:
        console.print("[red]GROQ_API_KEY is not set in spike/.env[/]")
        raise SystemExit(1)
    if len(sys.argv) > 1 and sys.argv[1] == "models":
        list_models()
    else:
        main(sys.argv[1:] or ["llama-3.3-70b-versatile"])
