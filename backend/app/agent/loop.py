"""The agent loop.

    goal -> think -> call a tool -> observe -> think -> ... -> answer

Synchronous and single-process on purpose. `run_agent(goal)` is a function
call you can read top to bottom and step through in a debugger. Async
execution, SSE, resumption and approval gates are all deferred with explicit
triggers — none of them teaches anything about how an agent reasons, and each
one hides the control flow behind machinery.

The one non-obvious property: the loop never raises. A provider outage, a
broken tool, a hallucinated tool name, an exhausted budget — every one of them
resolves to a RunResult with whatever the agent managed to establish. An agent
that dies with a stack trace has thrown away the work it already did.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.agent import prompts
from app.agent.selector import Decision, Outcome, next_action
from app.core.budget import BudgetState, RunBudget
from app.llm.base import LLMProvider, Message
from app.llm.manager import Mode, ResolvedProvider, RunContext, resolve
from app.models.run import Run, RunStatus
from app.models.step import StepKind
from app.repositories.run_repository import RunRepository
from app.tools.base import ToolResult
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry

# Warn the model once, this many steps from the end, so it can summarise what
# it has instead of being cut off mid-investigation.
WARN_AT_STEPS_REMAINING = 2

# Consecutive RETRY decisions tolerated before giving up. Small on purpose:
# each retry is a full LLM call, and a model that has failed to act three times
# running is not about to recover on the fourth.
MAX_CONSECUTIVE_RETRIES = 3


@dataclass
class RunResult:
    run_id: int
    status: RunStatus
    answer: str | None = None
    error: str | None = None
    steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_seconds: float = 0.0
    trace: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status is RunStatus.COMPLETED


def run_agent(
    db: Session,
    goal: str,
    *,
    context: RunContext | None = None,
    provider: LLMProvider | None = None,
    registry: ToolRegistry | None = None,
    budget: RunBudget | None = None,
    session_id: str | None = None,
    quota_check=None,
    on_step=None,
) -> RunResult:
    """Execute one goal to completion.

    Provider selection is delegated to the Provider Manager. This function
    receives an LLMProvider and a budget and cannot tell whether a hosted trial
    key or the user's own key paid for the run — which is the whole point of
    llm/manager.py being the single gated path to hosted credentials.

    `provider` is still accepted so tests can inject a scripted fake without
    going through resolution. When it is passed, `context` is ignored.

    `on_step(kind, payload)` lets a caller render the trace live. Not an event
    system — one function, called synchronously, which is all live rendering
    needs.
    """
    if registry is None:
        from app.tools import registry as default_registry

        registry = default_registry

    resolved: ResolvedProvider | None = None
    if provider is None:
        # May raise NoProviderAvailable — deliberately BEFORE the run row is
        # created. A refusal ("you are out of trial runs", "your key is
        # invalid") is not a failed run; recording it as one would corrupt the
        # success-rate metric with things the agent never attempted.
        resolved = resolve(context or RunContext(mode=Mode.TRIAL), quota_check=quota_check)
        provider = resolved.provider
        budget = budget or resolved.budget

    budget = budget or RunBudget.from_settings()

    run = RunRepository(db).create(
        goal,
        session_id=session_id,
        mode=resolved.mode.value if resolved else None,
        provider_name=resolved.provider_name if resolved else getattr(provider, "name", None),
        model=resolved.model if resolved else getattr(provider, "model", None),
        identity=(context.identity if context else None),
    )
    return execute_run(
        db, run, provider, registry, budget,
        label=resolved.label if resolved else getattr(provider, "model", "injected"),
        on_step=on_step,
    )


def execute_run(
    db: Session,
    run: Run,
    provider: LLMProvider,
    registry: ToolRegistry,
    budget: RunBudget,
    *,
    label: str = "",
    on_step=None,
) -> RunResult:
    """Run the loop against an ALREADY-CREATED run row.

    Split from run_agent so the API can create the run, return 202 with its id,
    and execute afterwards — a caller cannot be handed an id that does not
    exist yet, and the client can start streaming the trace immediately.
    """
    repo = RunRepository(db)
    executor = ToolExecutor(registry)
    # Per-run cache. Results are only valid within one run: the corpus or the
    # web may have changed since the last one.
    executor.reset()
    repo.start(run)

    goal = run.goal
    label = label or run.model or "unknown"

    messages: list[Message] = prompts.initial_messages(goal)
    consecutive_retries = 0
    warned = False
    # How many tool observations this run has gathered. Used only to decide
    # whether a text-only reply counts as a finished answer (selector.py).
    observations = 0

    def emit(kind: str, payload: dict) -> None:
        if on_step:
            on_step(kind, payload)

    emit("goal", {
        "goal": goal,
        "run_id": run.id,
        # Surfaced so a trace is self-explanatory: token counts and latency are
        # inexplicable without knowing which model produced them.
        "provider": label,
    })

    while True:
        state = budget.check()
        if state is not BudgetState.OK:
            reason = budget.describe(state)
            repo.add_step(run, StepKind.ERROR.value, error=reason)
            emit("budget", {"reason": reason})
            # A partial answer, not silence. The agent may have established
            # most of what was asked before running out.
            return _finish(
                repo, run, budget, RunStatus.FAILED,
                error=reason,
                answer=_best_effort(messages),
            )

        # One warning near the end so the model can wrap up deliberately.
        if not warned and budget.steps_remaining <= WARN_AT_STEPS_REMAINING:
            messages.append(Message(role="user", content=prompts.budget_warning(budget.steps_remaining)))
            warned = True

        budget.consume()
        # has_evidence changes how a text-only reply is read: once a tool has
        # produced an observation, any non-empty answer is legitimate however
        # short. See selector.py's no-tool-call branch.
        decision = next_action(provider, registry, messages, has_evidence=observations > 0)

        # --- the model answered -------------------------------------------
        if decision.outcome is Outcome.DONE:
            repo.add_step(
                run, StepKind.FINAL.value, output={"answer": decision.answer},
                model=decision.model, prompt_tokens=decision.prompt_tokens,
                completion_tokens=decision.completion_tokens, latency_ms=decision.latency_ms,
            )
            emit("final", {"answer": decision.answer})
            return _finish(repo, run, budget, RunStatus.COMPLETED, answer=decision.answer)

        # --- unrecoverable ------------------------------------------------
        if decision.outcome is Outcome.FAILED:
            repo.add_step(run, StepKind.ERROR.value, error=decision.error)
            emit("error", {"error": decision.error})
            return _finish(
                repo, run, budget, RunStatus.FAILED,
                error=decision.error, answer=_best_effort(messages),
            )

        # --- recoverable: feed the problem back and try again --------------
        if decision.outcome is Outcome.RETRY:
            consecutive_retries += 1
            repo.add_step(run, StepKind.ERROR.value, error=decision.error)
            emit("retry", {"error": decision.error, "attempt": consecutive_retries})

            if consecutive_retries >= MAX_CONSECUTIVE_RETRIES:
                return _finish(
                    repo, run, budget, RunStatus.FAILED,
                    error=f"Gave up after {consecutive_retries} consecutive failures: {decision.error}",
                    answer=_best_effort(messages),
                )
            # The error text IS the repair instruction — it tells the model
            # exactly what was wrong with its last turn.
            messages.append(Message(role="user", content=decision.error or "Try again."))
            continue

        consecutive_retries = 0

        # --- act ------------------------------------------------------------
        call = decision.tool_call
        assert call is not None  # Outcome.ACT guarantees this

        if decision.thought:
            repo.add_step(run, StepKind.THOUGHT.value, output={"text": decision.thought})
            emit("thought", {"text": decision.thought})

        repo.add_step(
            run, StepKind.TOOL_CALL.value, tool_name=call.name, input=call.arguments,
            model=decision.model, prompt_tokens=decision.prompt_tokens,
            completion_tokens=decision.completion_tokens, latency_ms=decision.latency_ms,
        )
        emit("tool_call", {"tool": call.name, "arguments": call.arguments})

        result = executor.execute(call.name, call.arguments)

        repo.add_step(
            run, StepKind.OBSERVATION.value, tool_name=call.name,
            output={"ok": result.ok, "unavailable": result.unavailable,
                    "data": result.data, "meta": result.meta},
            error=result.error,
            latency_ms=result.meta.get("latency_ms"),
        )
        emit("observation", {
            "tool": call.name, "ok": result.ok, "unavailable": result.unavailable,
            "summary": _summarise(result),
        })
        observations += 1

        # Both sides of the exchange go back into the transcript: what the
        # agent decided, and what came back — fenced as untrusted data.
        messages.append(Message(
            role="assistant",
            content=prompts.render_action(decision.thought, call.name, call.arguments),
        ))
        messages.append(Message(
            role="user",
            content=prompts.render_observation(
                call.name, _render_result(result), ok=result.ok, unavailable=result.unavailable
            ),
        ))


# --- helpers ---------------------------------------------------------------

def _render_result(result: ToolResult) -> str:
    """What the model sees. Prefers the tool's own `rendered` block, which
    carries attribution (document, page, url) alongside the text so the answer
    can cite it."""
    if not result.ok:
        return result.error or "The tool reported a failure with no detail."
    rendered = result.meta.get("rendered")
    if rendered:
        return rendered
    if result.data is None:
        return "(no data)"
    if isinstance(result.data, (str, int, float, bool)):
        return str(result.data)
    return json.dumps(result.data, ensure_ascii=False, default=str)[:4000]


def _summarise(result: ToolResult) -> str:
    if result.unavailable:
        return f"unavailable: {result.error}"
    if not result.ok:
        return f"failed: {result.error}"
    count = result.meta.get("count")
    if count is not None:
        return f"{count} result(s)"
    return str(result.data)[:120]


def _best_effort(messages: list[Message]) -> str | None:
    """When a run stops early, return the last thing the agent actually said.

    Not a fresh LLM call: the run stopped because it ran out of budget or the
    provider died, and both make another call the wrong move. This is honest
    salvage, not a summary.
    """
    for message in reversed(messages):
        if message.role == "assistant" and message.content.strip():
            return message.content.strip()
    return None


def _finish(
    repo: RunRepository,
    run: Run,
    budget: RunBudget,
    status: RunStatus,
    answer: str | None = None,
    error: str | None = None,
) -> RunResult:
    repo.finish(run, status, answer=answer, error=error)
    return RunResult(
        run_id=run.id,
        status=status,
        answer=answer,
        error=error,
        steps=run.step_count,
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        elapsed_seconds=budget.elapsed,
        trace=[
            {
                "idx": s.idx, "kind": s.kind, "tool": s.tool_name,
                "input": s.input, "output": s.output, "error": s.error,
            }
            for s in repo.steps(run.id)
        ],
    )
