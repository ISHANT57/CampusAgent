"""CampusBrain Agent CLI — the product for Phases D and E.

    python cli.py run "What is the minimum CGPA to keep my scholarship?"
    python cli.py run "..." --max-steps 5 --quiet
    python cli.py tools
    python cli.py runs --limit 5
    python cli.py trace 7

A terminal, not a browser: the loop will be rewritten many times before the
trace format settles, and a terminal iterates in seconds. The web UI is
deferred until there is something stable to render.

argparse, not Typer. Typer 0.12.5 mis-bound this file's parameters — it typed
`quiet: bool` as Click's `text`, so the body received the STRING "False",
which is truthy, and `--quiet` was permanently on. The whole live trace
vanished and only the final answer printed, with no error anywhere. That cost
more time to find than the flag parsing is worth. argparse is stdlib, infers
nothing from annotations, and cannot mis-bind.
"""

import argparse
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# emoji=False: model ids contain colons (":free"), which rich would otherwise
# expand as emoji shortcodes and then fail to encode on a Windows console.
console = Console(emoji=False)


def _render(kind: str, payload: dict) -> None:
    """Live trace rendering. Each step type is visually distinct at a glance,
    because you read this to find where a run went wrong — and that is usually
    visible a few steps before it fails."""
    if kind == "goal":
        console.print(Panel(payload["goal"], title=f"goal (run {payload['run_id']})", border_style="cyan"))
    elif kind == "thought":
        console.print(f"[dim]think   [/] {payload['text'][:400]}")
    elif kind == "tool_call":
        console.print(f"[yellow]call    [/] [bold]{payload['tool']}[/]({payload['arguments']})")
    elif kind == "observation":
        if payload["unavailable"]:
            colour, label = "red", "UNAVAILABLE"
        elif payload["ok"]:
            colour, label = "green", "ok"
        else:
            colour, label = "red", "failed"
        console.print(f"[{colour}]observe [/] [{colour}]{label}[/] {payload['summary'][:200]}")
    elif kind == "retry":
        console.print(f"[magenta]retry   [/] (attempt {payload['attempt']}) {payload['error'][:160]}")
    elif kind == "budget":
        console.print(f"[red]budget  [/] {payload['reason']}")
    elif kind == "error":
        console.print(f"[red]error   [/] {payload['error'][:200]}")
    elif kind == "final":
        console.print()
        console.print(Panel(payload["answer"], title="answer", border_style="green"))


def cmd_run(args: argparse.Namespace) -> int:
    from app.agent.loop import run_agent
    from app.core.budget import RunBudget
    from app.core.database import SessionLocal

    budget = RunBudget.from_settings()
    if args.max_steps:
        budget.max_steps = args.max_steps

    db = SessionLocal()
    try:
        result = run_agent(db, args.goal, budget=budget, on_step=None if args.quiet else _render)
    finally:
        db.close()

    if args.quiet:
        console.print(result.answer or result.error or "(no answer)")
        return 0 if result.ok else 1

    console.print()
    # ASCII separators only. The Windows console encodes as cp1252, which
    # cannot represent U+00B7 (·) and renders it as a replacement character.
    console.print(
        f"[dim]run {result.run_id} | {result.status.value} | {result.steps} steps | "
        f"{result.prompt_tokens}+{result.completion_tokens} tokens | "
        f"{result.elapsed_seconds:.1f}s[/]"
    )
    if result.error:
        console.print(f"[red]{result.error}[/]")
    return 0 if result.ok else 1


def cmd_tools(args: argparse.Namespace) -> int:
    """Show the tools exactly as the model sees them — descriptions included,
    because the description IS the selection algorithm (M0/F7)."""
    from app.tools import registry

    table = Table(title="registered tools")
    table.add_column("name", style="bold")
    table.add_column("timeout", justify="right")
    table.add_column("description", overflow="fold")
    for tool in registry.all():
        table.add_row(tool.name, f"{tool.timeout_s:.0f}s", tool.description)
    console.print(table)
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    from app.core.database import SessionLocal
    from app.repositories.run_repository import RunRepository

    db = SessionLocal()
    try:
        repo = RunRepository(db)
        run_row = repo.get(args.run_id)
        if run_row is None:
            console.print(f"[red]run {args.run_id} not found[/]")
            return 1

        console.print(Panel(run_row.goal, title=f"run {args.run_id} | {run_row.status}", border_style="cyan"))
        for step in repo.steps(args.run_id):
            detail = step.error or (str(step.output)[:200] if step.output else "")
            console.print(f"[dim]{step.idx:>3}[/] {step.kind:<12} {step.tool_name or '':<18} {detail}")
        if run_row.final_answer:
            console.print()
            console.print(Panel(run_row.final_answer, title="answer", border_style="green"))
        return 0
    finally:
        db.close()


def cmd_runs(args: argparse.Namespace) -> int:
    from app.core.database import SessionLocal
    from app.repositories.run_repository import RunRepository

    db = SessionLocal()
    try:
        table = Table(title="recent runs")
        for col in ("id", "status", "steps", "goal"):
            table.add_column(col, overflow="fold")
        for r in RunRepository(db).recent(args.limit):
            table.add_row(str(r.id), r.status, str(r.step_count), r.goal[:70])
        console.print(table)
        return 0
    finally:
        db.close()


def cmd_eval(args: argparse.Namespace) -> int:
    """Run the golden set and print the metrics (M39 + M41)."""
    from app.eval.runner import run_golden

    def progress(event: str, payload) -> None:
        if event == "start":
            console.print(f"[cyan]{payload['id']}[/] {payload['goal'][:72]}")
        else:
            s = payload["score"]
            tool = "[green]OK [/]" if s.tool_correct else "[red]X  [/]"
            ans = "" if s.answer_ok is None else (" ans:OK" if s.answer_ok else " [red]ans:X[/]")
            degraded = " [yellow](degraded)[/]" if s.tools_unavailable else ""
            console.print(
                f"      {tool} first={s.first_tool or '(none)'} want={s.expected_tool or '(none)'}"
                f" steps={s.steps}/{s.min_steps}{ans}{degraded}"
            )

    report = run_golden(only=args.only, on_progress=progress)

    console.print()
    table = Table(title="agent metrics")
    table.add_column("metric", style="bold")
    table.add_column("value", justify="right")
    for k, v in report.as_dict().items():
        table.add_row(k.replace("_", " "), str(v))
    console.print(table)

    if report.skipped:
        console.print(
            f"[dim]skipped {len(report.skipped)}: "
            + ", ".join(f"{g['id']} (needs {', '.join(g['missing_tools'])})" for g in report.skipped)
            + "[/]"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py", description="CampusBrain autonomous agent.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run the agent against one goal.")
    p_run.add_argument("goal", help="What you want the agent to accomplish.")
    p_run.add_argument("--max-steps", type=int, default=None, help="Override the step budget.")
    p_run.add_argument("-q", "--quiet", action="store_true", help="Only print the final answer.")
    p_run.set_defaults(func=cmd_run)

    p_tools = sub.add_parser("tools", help="List the tools the agent can choose from.")
    p_tools.set_defaults(func=cmd_tools)

    p_trace = sub.add_parser("trace", help="Replay a past run's trace.")
    p_trace.add_argument("run_id", type=int)
    p_trace.set_defaults(func=cmd_trace)

    p_runs = sub.add_parser("runs", help="List recent runs.")
    p_runs.add_argument("--limit", type=int, default=10)
    p_runs.set_defaults(func=cmd_runs)

    p_eval = sub.add_parser("eval", help="Run the golden set and report metrics.")
    p_eval.add_argument("--only", nargs="*", default=None, help="Restrict to these goal ids, e.g. G01 G04.")
    p_eval.set_defaults(func=cmd_eval)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
