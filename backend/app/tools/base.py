"""Tool contract.

The central rule: **no tool may raise.** Timeout, HTTP error, bad arguments,
quota exhaustion — all become a ToolResult the agent can read and reason about.
A tool that raises kills a run that may be 80% complete; a tool that returns
ok=False lets the agent try something else.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """What every tool returns. Failure is a value, not an exception."""

    ok: bool
    data: Any | None = None
    error: str | None = None

    # Why unavailable is separate from failed, rather than one `ok=False`:
    # the agent's correct response differs. `failed` means this approach is
    # wrong — replan. `unavailable` means the approach is fine but the
    # dependency is down — say so honestly instead of concluding the corpus
    # lacks the answer.
    #
    # M0/F9 is why this field exists: the spike's own E5 harness conflated the
    # two and reported five 429s as five model failures. A confident wrong
    # verdict is worse than no verdict.
    unavailable: bool = False

    meta: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def success(cls, data: Any, **meta: Any) -> "ToolResult":
        return cls(ok=True, data=data, meta=meta)

    @classmethod
    def failure(cls, error: str, **meta: Any) -> "ToolResult":
        return cls(ok=False, error=error, meta=meta)

    @classmethod
    def down(cls, error: str, **meta: Any) -> "ToolResult":
        """The tool itself is unreachable — quota, outage, timeout."""
        return cls(ok=False, error=error, unavailable=True, meta=meta)


class Tool(BaseModel):
    """A capability, as both the model and the executor see it."""

    model_config = {"arbitrary_types_allowed": True}

    name: str
    # NOT documentation. M0 traced all 15 wrong tool choices in 180 calls to
    # one word in one description. This field IS the selection algorithm.
    description: str
    args_model: type[BaseModel]
    fn: Callable[[BaseModel], ToolResult]

    timeout_s: float = 30.0

    # Safe to serve from cache when called twice with identical args inside a
    # run. Guards the most common loop pathology and saves free-tier quota.
    idempotent: bool = True

    # Ends the run when called. This is how the loop terminates: it asks the
    # TOOL whether the run is over rather than comparing the name to the
    # string "final_answer". Termination becomes an explicit property of the
    # vocabulary instead of a special case buried in the runtime.
    terminal: bool = False

    def json_schema(self) -> dict[str, Any]:
        """The parameter schema sent to the model, derived from the Pydantic
        args model — so validation and the prompt can never drift apart."""
        return self.args_model.model_json_schema()
