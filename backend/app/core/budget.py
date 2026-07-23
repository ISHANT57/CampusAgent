"""Run budget — the cheapest bug prevention in the project.

An agent without a step limit loops forever. Not hypothetically: the most
common pathology is calling the same tool with the same arguments repeatedly,
each call burning quota and wall-clock until something external kills it.

Two ceilings only, deliberately:

  max_steps      the loop stops thinking
  wall clock     the loop stops waiting

Token and cost accounting are DEFERRED. M0 measured Gemini at ~324-960 tokens
per call and p50 ~1.0s, so a 15-step run is roughly 15 seconds and 5-15k
tokens — not a number worth policing yet. A runaway loop is.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class BudgetState(str, Enum):
    OK = "ok"
    STEPS_EXHAUSTED = "steps_exhausted"
    TIME_EXHAUSTED = "time_exhausted"


@dataclass
class RunBudget:
    max_steps: int = 15
    max_wall_clock_seconds: float = 300.0

    steps_used: int = 0
    # Not Field(default_factory=time.monotonic) on the class: the clock must
    # start when the RUN starts, not when the module is imported.
    started_at: float = field(default_factory=time.monotonic)

    @classmethod
    def from_settings(cls) -> "RunBudget":
        from app.core.config import get_settings

        s = get_settings()
        return cls(max_steps=s.max_steps, max_wall_clock_seconds=float(s.max_wall_clock_seconds))

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def steps_remaining(self) -> int:
        return max(0, self.max_steps - self.steps_used)

    def check(self) -> BudgetState:
        """Called before each iteration. Never raises — the loop needs to
        finish gracefully with a partial answer, not blow up."""
        if self.steps_used >= self.max_steps:
            return BudgetState.STEPS_EXHAUSTED
        if self.elapsed >= self.max_wall_clock_seconds:
            return BudgetState.TIME_EXHAUSTED
        return BudgetState.OK

    def consume(self) -> None:
        self.steps_used += 1

    def describe(self, state: BudgetState) -> str:
        """Human-readable reason, used both in the trace and in the partial
        answer shown to the user. A run that stops must always say why."""
        if state is BudgetState.STEPS_EXHAUSTED:
            return f"Reached the step limit ({self.max_steps} steps)."
        if state is BudgetState.TIME_EXHAUSTED:
            return f"Reached the time limit ({self.max_wall_clock_seconds:.0f}s, used {self.elapsed:.0f}s)."
        return "Budget OK."
