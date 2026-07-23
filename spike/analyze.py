"""Score the E3/E4 corpus and check every M0 gate.

Reads results/e3_compliance.jsonl. Pure analysis — no network, so the whole
corpus can be re-scored for free after adding a failure class.

Also emits fixtures_failures.json: the curated failure corpus that M10's
prompted-JSON adapter is tested against. That file IS committed; the raw
multi-megabyte jsonl is not.
"""

from __future__ import annotations

import json
import pathlib
from collections import defaultdict

from rich.console import Console
from rich.table import Table

from fixtures import GOALS

# emoji=False: model ids end in ":free", and "{model}: " renders as ":free: ",
# which rich expands to the 🆓 emoji — then the Windows console's cp1252 codec
# cannot encode it and the whole report dies mid-print.
console = Console(emoji=False)
HERE = pathlib.Path(__file__).parent
ROWS = [
    json.loads(line)
    for line in (HERE / "results" / "e3_compliance.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]

GOAL_BY_ID = {g["id"]: g for g in GOALS}

# Gate thresholds, fixed in the M0 design BEFORE any data existed.
GATE_FORMAT = 95.0
GATE_FORMAT_DEGRADED = 90.0
GATE_SELECTION = 80.0
GATE_OVERLAP = 70.0


def pct(n: int, d: int) -> float:
    return 100.0 * n / d if d else 0.0


# --- per-model summary -------------------------------------------------------

by_model: dict[str, list[dict]] = defaultdict(list)
for r in ROWS:
    by_model[r["model"]].append(r)

console.print(f"\n[bold]E3/E4 — {len(ROWS)} calls across {len(by_model)} models[/]\n")

table = Table(title="Gates 1 & 3 — availability, format compliance, selection accuracy")
for col, just in (("model", "left"), ("n", "right"), ("reached", "right"),
                  ("format %", "right"), ("selection %", "right"),
                  ("p50 ms", "right"), ("avg tok/call", "right"), ("verdict", "left")):
    table.add_column(col, justify=just, overflow="fold")

summary: dict[str, dict] = {}

for model, rows in by_model.items():
    n = len(rows)

    # A 429 never reached the model. Counting it as a format failure conflates
    # AVAILABILITY (a quota/billing property) with CAPABILITY (whether the model
    # can emit a parseable tool call) — two different problems with two
    # different fixes. Format compliance is scored only over calls the model
    # actually answered.
    reached = [r for r in rows if r["format_class"] != "API_ERROR"]
    ok = sum(1 for r in reached if r["format_class"] == "OK")
    scored = [r for r in rows if r["selection_ok"] is not None]
    right = sum(1 for r in scored if r["selection_ok"])

    fmt = pct(ok, len(reached))
    sel = pct(right, len(scored))
    avail = pct(len(reached), n)

    lat = sorted(r["latency_ms"] for r in rows if r.get("latency_ms"))
    p50 = lat[len(lat) // 2] if lat else 0
    toks = [
        (r["peek"]["usage"].get("prompt") or 0) + (r["peek"]["usage"].get("completion") or 0)
        for r in rows if r.get("peek")
    ]
    avg_tok = sum(toks) / len(toks) if toks else 0

    if len(reached) < 10:
        verdict = "[yellow]INSUFFICIENT DATA[/] (quota)"
    elif fmt >= GATE_FORMAT and sel >= GATE_SELECTION:
        verdict = "[green]PASS[/]"
    elif fmt < GATE_FORMAT_DEGRADED:
        verdict = "[red]FAIL format[/]"
    elif sel < GATE_SELECTION:
        verdict = "[yellow]weak selection[/]"
    else:
        verdict = "[yellow]needs repair loop[/]"

    summary[model] = {
        "n": n, "reached": len(reached), "availability": avail,
        "format": fmt, "selection": sel, "p50_ms": p50, "avg_tokens": avg_tok,
    }
    table.add_row(
        model, str(n), f"{len(reached)} ({avail:.0f}%)",
        f"{fmt:.1f}", f"{sel:.1f}", str(p50), f"{avg_tok:.0f}", verdict,
    )

console.print(table)

# --- failure classes ---------------------------------------------------------

console.print("\n[bold]Failure classes per model[/] (format failures only)")
for model, rows in by_model.items():
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["format_class"] != "OK":
            counts[r["format_class"]] += 1
    if counts:
        console.print(f"  {model}: {dict(sorted(counts.items(), key=lambda kv: -kv[1]))}")
    else:
        console.print(f"  {model}: [green]no format failures[/]")

# --- per-goal difficulty -----------------------------------------------------

console.print("\n[bold]Selection accuracy per goal[/] (which goals are actually hard)")
gt = Table()
for c in ("goal", "kind", "expected", "correct/scored", "%"):
    gt.add_column(c, overflow="fold")

for g in GOALS:
    rows = [r for r in ROWS if r["goal_id"] == g["id"] and r["selection_ok"] is not None]
    right = sum(1 for r in rows if r["selection_ok"])
    rate = pct(right, len(rows))
    colour = "green" if rate >= 80 else ("yellow" if rate >= 50 else "red")
    gt.add_row(g["id"], g["kind"], g["expected"], f"{right}/{len(rows)}", f"[{colour}]{rate:.0f}[/]")
console.print(gt)

# what did they choose instead?
console.print("\n[bold]Wrong choices[/] (goal -> what was called instead)")
wrong: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
for r in ROWS:
    if r["selection_ok"] is False:
        chosen = (r["peek"]["tool_calls"] or [{}])[0].get("name")
        wrong[r["goal_id"]][chosen] += 1
for gid, choices in sorted(wrong.items()):
    console.print(f"  {gid} (want {GOAL_BY_ID[gid]['expected']}): {dict(choices)}")

# --- Gate 4: are failures uncorrelated? --------------------------------------

console.print("\n[bold]Gate 4 — failure-set overlap between providers[/]")
console.print("[dim]A fallback only helps if it succeeds where the primary fails.[/]")

# API_ERROR excluded here too. Two models throttled during the same wall-clock
# window would show near-total "failure" overlap that says nothing about the
# models — only that they shared a clock. Correlated QUOTA is a real risk, but
# it is a separate finding from correlated CAPABILITY, and mixing them would
# make an uncorrelated fallback look correlated.
fail_sets: dict[str, set[str]] = {}
for model, rows in by_model.items():
    fail_sets[model] = {
        f"{r['goal_id']}#{r['trial']}"
        for r in rows
        if (r["format_class"] not in ("OK", "API_ERROR")) or r["selection_ok"] is False
    }

models = list(fail_sets)
for i, a in enumerate(models):
    for b in models[i + 1 :]:
        fa, fb = fail_sets[a], fail_sets[b]
        if not fa and not fb:
            console.print(f"  {a} vs {b}: [green]neither failed[/]")
            continue
        inter = len(fa & fb)
        union = len(fa | fb)
        ov = pct(inter, union)
        colour = "red" if ov > GATE_OVERLAP else "green"
        console.print(
            f"  {a} vs {b}: overlap [{colour}]{ov:.0f}%[/] "
            f"({inter} shared / {union} union; {len(fa)} vs {len(fb)} failures)"
        )

# --- M10 fixture corpus ------------------------------------------------------

failures = [
    {
        "model": r["model"],
        "provider": r["provider"],
        "goal_id": r["goal_id"],
        "format_class": r["format_class"],
        "format_detail": r["format_detail"],
        "recoverable": r["recoverable"],
        "raw_text": (r["peek"] or {}).get("text"),
        "raw_tool_calls": (r["peek"] or {}).get("tool_calls"),
        "finish_reason": (r["peek"] or {}).get("finish_reason"),
    }
    for r in ROWS
    if r["format_class"] not in ("OK", "API_ERROR")
]

out = HERE / "fixtures_failures.json"
out.write_text(json.dumps(failures, indent=2, ensure_ascii=False), encoding="utf-8")
console.print(f"\n[dim]{len(failures)} real failure samples -> {out.name} (M10 test fixtures)[/]")

(HERE / "results" / "summary.json").write_text(
    json.dumps(summary, indent=2), encoding="utf-8"
)
