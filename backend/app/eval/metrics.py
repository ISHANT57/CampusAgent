"""Agent metrics, computed entirely from the trace.

No instrumentation exists anywhere in the codebase, and none is needed. The
`steps` table already records every thought, tool call, observation and answer,
because it has to — the prompt is assembled from it. Metrics are a read over
data that was going to exist regardless.

Why agent evaluation is harder than model evaluation, and why these metrics
are shaped the way they are: the TRAJECTORY matters, not just the answer. An
agent that reaches the right answer through six wasted tool calls is worse
than one that takes two, and outcome-only scoring cannot see the difference.
So success and efficiency are measured separately, and tool selection is
scored apart from both.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

GOLDEN_PATH = pathlib.Path(__file__).parent / "golden.json"


def load_golden() -> list[dict]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["goals"]


def runnable_goals(registered_tools: set[str]) -> tuple[list[dict], list[dict]]:
    """Split the golden set into what can be scored now and what cannot.

    A goal requiring an unregistered tool is SKIPPED, not failed. Scoring G02
    as a failure because knowledge_list_documents does not exist yet would
    depress the success rate for a reason that has nothing to do with the
    agent — and would quietly hide real regressions behind a known-bad number.
    """
    runnable, skipped = [], []
    for goal in load_golden():
        missing = set(goal.get("requires", [])) - registered_tools
        (skipped if missing else runnable).append(
            {**goal, "missing_tools": sorted(missing)} if missing else goal
        )
    return runnable, skipped


@dataclass
class GoalScore:
    goal_id: str
    completed: bool
    first_tool: str | None
    expected_tool: str | None
    tool_correct: bool | None          # None = not scored
    steps: int
    min_steps: int
    answer_ok: bool | None             # None = nothing asserted
    tools_unavailable: int
    tokens: int
    seconds: float
    answer: str | None = None
    error: str | None = None

    @property
    def efficiency(self) -> float | None:
        """min_steps / actual. 1.0 is optimal, lower means wasted work."""
        if not self.steps:
            return None
        return round(self.min_steps / self.steps, 2)


def score_goal(goal: dict, result) -> GoalScore:
    """Score one RunResult against its golden entry. Pure — no database, no
    network — so it can be unit-tested and re-run over recorded traces."""
    tool_calls = [s for s in result.trace if s["kind"] == "tool_call"]
    first_tool = tool_calls[0]["tool"] if tool_calls else None

    expected = goal["expected_tool"]
    acceptable = {expected, *goal.get("acceptable_tools", [])} - {None}

    if not result.ok and first_tool is None:
        # The run died before choosing anything — a provider 429, typically.
        # UNSCORED, not correct and not wrong: we have no evidence about what
        # it would have selected.
        #
        # This case is why the check exists. For a goal whose correct move is
        # NO tool (G09), `first_tool is None` would otherwise read as a correct
        # decision, and an infrastructure outage would inflate the selection
        # score. A metric that improves when the provider goes down is worse
        # than no metric.
        tool_correct = None
    elif expected is None:
        # The correct move is no tool. Any tool call is a selection failure.
        tool_correct = first_tool is None
    elif first_tool is None:
        tool_correct = False
    else:
        tool_correct = first_tool in acceptable

    answer = result.answer or ""
    needles = goal.get("answer_contains") or []
    answer_ok = all(n.lower() in answer.lower() for n in needles) if needles else None

    unavailable = sum(
        1
        for s in result.trace
        if s["kind"] == "observation" and (s.get("output") or {}).get("unavailable")
    )

    return GoalScore(
        goal_id=goal["id"],
        completed=result.ok,
        first_tool=first_tool,
        expected_tool=expected,
        tool_correct=tool_correct,
        steps=result.steps,
        min_steps=goal.get("min_steps", 1),
        answer_ok=answer_ok,
        tools_unavailable=unavailable,
        tokens=result.prompt_tokens + result.completion_tokens,
        seconds=round(result.elapsed_seconds, 1),
        answer=result.answer,
        error=result.error,
    )


@dataclass
class Report:
    scores: list[GoalScore] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    def _rate(self, predicate) -> tuple[int, int]:
        considered = [s for s in self.scores if predicate(s) is not None]
        return sum(1 for s in considered if predicate(s)), len(considered)

    @property
    def success(self) -> tuple[int, int]:
        return sum(1 for s in self.scores if s.completed), len(self.scores)

    @property
    def tool_accuracy(self) -> tuple[int, int]:
        return self._rate(lambda s: s.tool_correct)

    @property
    def answer_accuracy(self) -> tuple[int, int]:
        """Only over goals that asserted something. Silence is not a pass."""
        return self._rate(lambda s: s.answer_ok)

    @property
    def mean_efficiency(self) -> float | None:
        vals = [s.efficiency for s in self.scores if s.efficiency is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    @property
    def degraded_runs(self) -> int:
        """Runs where at least one tool was UNAVAILABLE.

        Reported separately because a low success rate caused by a dependency
        outage is a different problem from one caused by bad reasoning, and
        averaging them together hides both.
        """
        return sum(1 for s in self.scores if s.tools_unavailable)

    @property
    def total_tokens(self) -> int:
        return sum(s.tokens for s in self.scores)

    def as_dict(self) -> dict:
        sr, st = self.success
        tr, tt = self.tool_accuracy
        ar, at = self.answer_accuracy
        return {
            "goals_scored": len(self.scores),
            "goals_skipped": len(self.skipped),
            "success_rate": _pct(sr, st),
            "tool_selection_accuracy": _pct(tr, tt),
            "answer_accuracy": _pct(ar, at),
            "mean_step_efficiency": self.mean_efficiency,
            "degraded_runs": self.degraded_runs,
            "total_tokens": self.total_tokens,
        }


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"
