"""Run the golden set and score it.

Deliberately a script, not a pytest suite. Each goal costs several live LLM
calls, and M0 established that free-tier quota is this project's binding
constraint — a test suite that burns quota gets run once and then avoided,
which is worse than no suite. The offline unit tests cover the scoring logic;
this runs the real agent when you want a real number.
"""

from __future__ import annotations

import time

from app.agent.loop import run_agent
from app.core.database import SessionLocal
from app.eval.metrics import Report, runnable_goals, score_goal
from app.tools import registry


def run_golden(
    only: list[str] | None = None,
    pause: float = 2.0,
    on_progress=None,
) -> Report:
    """Execute each runnable golden goal and score it.

    `pause` between goals because every goal is a burst of provider calls and
    free tiers rate-limit per minute; without it a later goal fails for reasons
    that have nothing to do with the agent.
    """
    registered = set(registry.names())
    goals, skipped = runnable_goals(registered)
    if only:
        goals = [g for g in goals if g["id"] in only]

    report = Report(skipped=skipped)
    db = SessionLocal()
    try:
        for i, goal in enumerate(goals):
            if on_progress:
                on_progress("start", goal)
            result = run_agent(db, goal["goal"])
            score = score_goal(goal, result)
            report.scores.append(score)
            if on_progress:
                on_progress("done", {"goal": goal, "score": score})
            if i < len(goals) - 1:
                time.sleep(pause)
    finally:
        db.close()
    return report
