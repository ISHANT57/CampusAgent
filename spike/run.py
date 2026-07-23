"""M0 experiment runner.

    python run.py e2          native tool-call support matrix   (1 call/model)
    python run.py e3          format compliance + selection     (12 goals x N trials)
    python run.py e5          multi-turn tool loop              (HARD GATE)
    python run.py e6          schema dialect probe

E3 and E4 share one set of calls: every reply is classified for format AND
scored for selection. Two experiments, one quota spend.
"""

from __future__ import annotations

import argparse
import json
import time

from rich.console import Console

from classify import classify, OK
from fixtures import GOALS, SYSTEM_PROMPT, TOOLS
from providers import (
    GEMINI_MODELS,
    OPENROUTER_MODELS,
    SLEEP,
    call_gemini,
    call_openrouter,
    peek,
    record,
)

console = Console()


def targets() -> list[tuple[str, str]]:
    return [("openrouter", m) for m in OPENROUTER_MODELS] + [("gemini", m) for m in GEMINI_MODELS]


def ask(provider: str, model: str, messages: list[dict], tools=TOOLS) -> tuple[dict, dict]:
    fn = call_openrouter if provider == "openrouter" else call_gemini
    env = fn(model, messages, tools=tools)
    return env, peek(env)


def msgs(goal_text: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": goal_text},
    ]


# ---------------------------------------------------------------------------
# E2 — does the model emit tool_calls at all?
#
# Three outcomes matter, and the third is the dangerous one:
#   native      -> returned a structured tool call
#   text-only   -> accepted `tools` without error, then IGNORED it. A silent
#                  no-op is worse than an error: it looks like the model simply
#                  chose not to call anything.
#   rejected    -> refused the request outright
# ---------------------------------------------------------------------------

def e2() -> None:
    goal = GOALS[0]
    console.print(f"[bold]E2[/] — tool-call support. Probe goal: {goal['goal']!r}\n")
    console.print(f"{'provider':11s} {'model':40s} {'status':>6}  verdict")
    console.print("-" * 100)

    for provider, model in targets():
        env, view = ask(provider, model, msgs(goal["goal"]))
        result = classify(env, view, goal)
        record("e2_tool_support", {**env, "peek": view, "goal_id": goal["id"], **result})

        if not env["ok"]:
            verdict = f"[red]API ERROR[/] {result['format_detail'][:60]}"
        elif view["tool_calls"]:
            names = [c["name"] for c in view["tool_calls"]]
            verdict = f"[green]NATIVE[/] -> {names}"
        else:
            verdict = f"[yellow]TEXT-ONLY (tools ignored)[/] {(view['text'] or '')[:50]!r}"

        console.print(f"{provider:11s} {model:40s} {str(env['status']):>6}  {verdict}")
        time.sleep(SLEEP)


# ---------------------------------------------------------------------------
# E3 + E4 — the core numbers
# ---------------------------------------------------------------------------

def e3(trials: int, only: list[str] | None) -> None:
    tgts = [t for t in targets() if not only or t[1] in only]
    total = len(tgts) * len(GOALS) * trials
    console.print(f"[bold]E3/E4[/] — {len(tgts)} models x {len(GOALS)} goals x {trials} trials = {total} calls\n")

    for provider, model in tgts:
        fmt_counts: dict[str, int] = {}
        sel_right = sel_scored = 0

        for goal in GOALS:
            for trial in range(trials):
                env, view = ask(provider, model, msgs(goal["goal"]))
                result = classify(env, view, goal)
                record("e3_compliance", {
                    **env, "peek": view, "goal_id": goal["id"], "trial": trial, **result
                })

                fmt_counts[result["format_class"]] = fmt_counts.get(result["format_class"], 0) + 1
                if result["selection_ok"] is not None:
                    sel_scored += 1
                    sel_right += int(result["selection_ok"])

                mark = "." if result["format_class"] == OK and result["selection_ok"] else "x"
                console.print(f"[dim]{mark}[/]", end="")
                time.sleep(SLEEP)

        n = sum(fmt_counts.values())
        fmt_rate = 100 * fmt_counts.get(OK, 0) / n if n else 0
        sel_rate = 100 * sel_right / sel_scored if sel_scored else 0
        console.print(
            f"\n[bold]{model}[/]  format {fmt_rate:.1f}%  selection {sel_rate:.1f}%"
            f"  ({sel_right}/{sel_scored})  {json.dumps(fmt_counts)}\n"
        )


# ---------------------------------------------------------------------------
# E5 — multi-turn. HARD GATE.
#
# Turn 1: model calls a tool. We feed back a synthetic result. Turn 2: does it
# take a correct SECOND action, or stall / repeat / forget?
#
# This is the real agent scenario. Models routinely handle turn 1 and degrade
# sharply on turn 2, and no turn-1 score compensates for that.
# ---------------------------------------------------------------------------

TOOL_RESULT = (
    "knowledge_search returned 1 passage:\n"
    '[1] (document 3, page 2, score 0.81) "Applicants must appear for JEE Mains 2026; '
    'a percentile of 85 or above is generally expected."'
)


def e5() -> None:
    goal = next(g for g in GOALS if g["id"] == "G12")
    console.print(f"[bold]E5[/] — multi-turn (HARD GATE). Goal: {goal['goal']!r}\n")

    for provider, model in targets():
        env1, view1 = ask(provider, model, msgs(goal["goal"]))
        record("e5_multiturn", {**env1, "peek": view1, "turn": 1, "model": model})

        # HARNESS BUG FIXED HERE: the first version reported any empty
        # tool_calls as "FAIL turn 1", which silently reported five 429s as
        # five model failures — a quota problem dressed up as a capability
        # problem. A gate that cannot tell "the model got it wrong" from "the
        # model never saw the request" is worse than no gate: it produces
        # confident, wrong verdicts. Availability is checked FIRST, always.
        if not env1["ok"]:
            raw = env1.get("raw") or {}
            why = str((raw.get("error") or {}).get("message") or env1.get("error"))[:70]
            console.print(f"{provider:11s} {model:40s} [yellow]SKIP[/] {env1['status']}: {why}")
            time.sleep(SLEEP)
            continue

        if not view1["tool_calls"]:
            console.print(f"{provider:11s} {model:40s} [red]FAIL turn 1[/] no tool call")
            time.sleep(SLEEP)
            continue

        first = view1["tool_calls"][0]["name"]

        # Turn 2. The tool result is presented as an assistant/user exchange
        # rather than a native role:"tool" message, so the SAME conversation
        # shape works on both providers. Whether native tool-result messages
        # do better is an M6 question, not an M0 one.
        convo = msgs(goal["goal"]) + [
            {"role": "assistant", "content": f"I called {first}."},
            {"role": "user", "content": f"Tool result:\n{TOOL_RESULT}\n\nContinue. Take your next action."},
        ]
        env2, view2 = ask(provider, model, convo)
        result2 = classify(env2, view2, goal)
        record("e5_multiturn", {**env2, "peek": view2, "turn": 2, "model": model, **result2})

        if not env2["ok"]:
            raw = env2.get("raw") or {}
            why = str((raw.get("error") or {}).get("message") or env2.get("error"))[:70]
            console.print(f"{provider:11s} {model:40s} [yellow]SKIP turn 2[/] {env2['status']}: {why}")
            time.sleep(SLEEP)
            continue

        if not view2["tool_calls"]:
            verdict = f"[red]FAIL turn 2[/] no call: {(view2['text'] or '')[:60]!r}"
        else:
            second = view2["tool_calls"][0]["name"]
            if second == first:
                verdict = f"[red]FAIL[/] repeated {second} (stuck)"
            elif second in {"calculator", "final_answer"}:
                verdict = f"[green]PASS[/] {first} -> {second}"
            else:
                verdict = f"[yellow]WEAK[/] {first} -> {second}"

        console.print(f"{provider:11s} {model:40s} {verdict}")
        time.sleep(SLEEP)


# ---------------------------------------------------------------------------
# E6 — schema dialect. Does a nested/enum/array schema survive each provider?
# ---------------------------------------------------------------------------

PROBE = [{
    "type": "function",
    "function": {
        "name": "schema_probe",
        "description": "Test tool. Call it with any plausible values.",
        "parameters": {
            "type": "object",
            "properties": {
                "required_string": {"type": "string", "description": "any text"},
                "optional_int": {"type": "integer", "description": "any number"},
                "an_enum": {"type": "string", "enum": ["alpha", "beta"], "description": "pick one"},
                "an_array": {"type": "array", "items": {"type": "string"}, "description": "list of words"},
                "nested": {
                    "type": "object",
                    "description": "a nested object",
                    "properties": {"inner": {"type": "string", "description": "inner text"}},
                },
            },
            "required": ["required_string"],
        },
    },
}]


def e6() -> None:
    console.print("[bold]E6[/] — schema dialect (enum / array / nested object)\n")
    probe_msgs = [{"role": "user", "content": "Call schema_probe with plausible values for every field."}]

    for provider, model in targets():
        env, view = ask(provider, model, probe_msgs, tools=PROBE)
        record("e6_schema", {**env, "peek": view, "model": model})

        if not env["ok"]:
            raw = env.get("raw") or {}
            msg = (raw.get("error") or {}).get("message") or env.get("error")
            console.print(f"{provider:11s} {model:40s} [red]{env['status']}[/] {str(msg)[:80]}")
        elif view["tool_calls"]:
            args = view["tool_calls"][0].get("arguments") or {}
            got = sorted(args) if isinstance(args, dict) else "non-dict"
            console.print(f"{provider:11s} {model:40s} [green]OK[/] fields={got}")
        else:
            console.print(f"{provider:11s} {model:40s} [yellow]no call[/] {(view['text'] or '')[:60]!r}")
        time.sleep(SLEEP)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("experiment", choices=["e2", "e3", "e5", "e6"])
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--only", nargs="*", help="restrict to these model ids")
    a = p.parse_args()

    {"e2": e2, "e5": e5, "e6": e6}.get(a.experiment, lambda: e3(a.trials, a.only))()
