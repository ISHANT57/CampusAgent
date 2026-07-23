"""M19-M23 tests. No network, no database.

The loop is driven by a scripted fake provider, so every branch — including the
ones that only happen when things go wrong — is reachable deterministically.
"""

import pytest
from pydantic import BaseModel

from app.agent import prompts
from app.agent.selector import MIN_OPENING_ANSWER_CHARS, Outcome, next_action
from app.core.budget import BudgetState, RunBudget
from app.llm.base import (
    Completion,
    LLMPermanentError,
    LLMTransientError,
    Message,
    ToolCall,
    Usage,
)
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry


class EchoArgs(BaseModel):
    text: str


def _registry() -> ToolRegistry:
    r = ToolRegistry()

    @r.register(description="Echo the given text back.")
    def echo(args: EchoArgs) -> ToolResult:
        return ToolResult.success(args.text, count=1)

    return r


class ScriptedProvider:
    """Returns a queued Completion (or raises a queued exception) per call."""

    name, model = "fake", "fake-1"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def complete(self, messages, tools=None, temperature=0.0):
        self.calls += 1
        item = self.script.pop(0) if self.script else Completion(text="fallback " * 20)
        if isinstance(item, Exception):
            raise item
        return item


def _call(name="echo", **args) -> Completion:
    return Completion(tool_calls=[ToolCall(name=name, arguments=args)], usage=Usage(prompt_tokens=1, completion_tokens=1))


def _answer(text: str) -> Completion:
    return Completion(text=text, usage=Usage(prompt_tokens=1, completion_tokens=1))


# --- M19 budget -------------------------------------------------------------

def test_budget_stops_at_max_steps():
    b = RunBudget(max_steps=2, max_wall_clock_seconds=999)
    assert b.check() is BudgetState.OK
    b.consume()
    b.consume()
    assert b.check() is BudgetState.STEPS_EXHAUSTED
    assert "step limit" in b.describe(b.check())


def test_budget_stops_on_wall_clock():
    b = RunBudget(max_steps=99, max_wall_clock_seconds=0.0)
    assert b.check() is BudgetState.TIME_EXHAUSTED


def test_steps_remaining_never_negative():
    b = RunBudget(max_steps=1)
    b.consume()
    b.consume()
    assert b.steps_remaining == 0


# --- M20 prompts and framing ------------------------------------------------

def test_observations_are_fenced_and_marked_untrusted():
    out = prompts.render_observation("web_search", "some page text", ok=True, unavailable=False)
    assert 'trusted="false"' in out
    assert 'source="web_search"' in out
    assert "</observation>" in out


def test_the_three_outcomes_render_differently():
    ok = prompts.render_observation("t", "x", ok=True, unavailable=False)
    failed = prompts.render_observation("t", "x", ok=False, unavailable=False)
    down = prompts.render_observation("t", "x", ok=False, unavailable=True)
    assert 'status="ok"' in ok
    assert 'status="failed"' in failed
    # The distinction the agent must not lose: down != empty corpus.
    assert 'status="unavailable"' in down


def test_system_prompt_tells_the_model_observations_are_not_instructions():
    assert "NOT instructions" in prompts.SYSTEM_PROMPT
    assert "<observation>" in prompts.SYSTEM_PROMPT


# --- M21 selector -----------------------------------------------------------

def test_tool_call_becomes_act():
    d = next_action(ScriptedProvider([_call(text="hi")]), _registry(), [])
    assert d.outcome is Outcome.ACT
    assert d.tool_call.name == "echo"


def test_substantive_text_without_a_tool_call_is_done():
    # The termination mechanism: the model stopped requesting tools.
    text = "The minimum CGPA required to retain the scholarship is 6.5 per the policy [1]."
    d = next_action(ScriptedProvider([_answer(text)]), _registry(), [])
    assert d.outcome is Outcome.DONE
    assert d.answer == text


def test_trivial_opening_reply_is_a_retry_not_a_silent_success():
    """M0's NO_CALL failure class, on turn one.

    A model that loses the thread and says "Okay." before doing any work must
    not be read as having finished — the run would report a successful answer
    it never grounded.
    """
    d = next_action(ScriptedProvider([_answer("Okay.")]), _registry(), [], has_evidence=False)
    assert d.outcome is Outcome.RETRY


def test_short_answer_AFTER_evidence_is_accepted():
    """REGRESSION: a live run failed on 'what is 6.5 minus 6.2?'.

    The agent called calculator, got 0.3, answered "0.3" — and a blanket length
    threshold rejected it three times until the run failed. A correct answer is
    not obliged to be long. Once the model has done the work, any non-empty
    reply is a legitimate conclusion.
    """
    d = next_action(ScriptedProvider([_answer("0.3")]), _registry(), [], has_evidence=True)
    assert d.outcome is Outcome.DONE
    assert d.answer == "0.3"


def test_empty_response_is_a_retry_even_with_evidence():
    d = next_action(ScriptedProvider([Completion(text="")]), _registry(), [], has_evidence=True)
    assert d.outcome is Outcome.RETRY


def test_opening_threshold_boundary():
    reg = _registry()
    long_enough = "x" * MIN_OPENING_ANSWER_CHARS
    too_short = "x" * (MIN_OPENING_ANSWER_CHARS - 1)
    # No evidence: length is the only signal available.
    assert next_action(ScriptedProvider([_answer(long_enough)]), reg, []).outcome is Outcome.DONE
    assert next_action(ScriptedProvider([_answer(too_short)]), reg, []).outcome is Outcome.RETRY
    # With evidence: length is irrelevant.
    assert next_action(ScriptedProvider([_answer(too_short)]), reg, [], has_evidence=True).outcome is Outcome.DONE


def test_unknown_tool_is_a_retry_with_the_real_names():
    d = next_action(ScriptedProvider([_call(name="nope")]), _registry(), [])
    assert d.outcome is Outcome.RETRY
    assert "echo" in d.error


def test_transient_provider_error_is_a_retry():
    d = next_action(ScriptedProvider([LLMTransientError("429")]), _registry(), [])
    assert d.outcome is Outcome.RETRY


def test_permanent_provider_error_is_fatal():
    # M0/F2: retrying a structurally-zero quota can never succeed and would
    # burn the run's remaining budget.
    d = next_action(ScriptedProvider([LLMPermanentError("limit: 0")]), _registry(), [])
    assert d.outcome is Outcome.FAILED


def test_selector_never_raises_even_on_an_unrecognised_exception():
    # A provider is third-party code and can raise outside our hierarchy. If
    # that escapes, the run dies with a stack trace instead of returning what
    # it had already established.
    d = next_action(ScriptedProvider([RuntimeError("boom")]), _registry(), [])
    assert d.outcome is Outcome.FAILED
    assert "RuntimeError" in d.error


# --- M23 loop (in-memory database) ------------------------------------------

@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.database import Base
    import app.models  # noqa: F401  registers the tables

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _run(db, script, goal="test goal", **kw):
    from app.agent.loop import run_agent

    return run_agent(db, goal, provider=ScriptedProvider(script), registry=_registry(), **kw)


def test_single_tool_call_then_answer(db):
    result = _run(db, [_call(text="hello"), _answer("The echo returned hello, which answers the goal.")])
    assert result.ok
    assert "hello" in result.answer
    kinds = [s["kind"] for s in result.trace]
    assert "tool_call" in kinds and "observation" in kinds and "final" in kinds


def test_multi_step_run(db):
    result = _run(db, [
        _call(text="one"),
        _call(text="two"),
        _answer("Both echoes completed, so the goal is satisfied and here is the summary."),
    ])
    assert result.ok
    assert [s["kind"] for s in result.trace].count("tool_call") == 2


def test_budget_exhaustion_ends_the_run_with_a_partial_answer(db):
    # An infinite tool-calling model. Without the budget this never returns.
    result = _run(db, [_call(text="x")] * 50, budget=RunBudget(max_steps=3, max_wall_clock_seconds=99))
    assert not result.ok
    assert "step limit" in result.error
    # Partial, not silence — the agent may have established most of the answer.
    assert result.answer is not None


def test_permanent_provider_failure_ends_the_run_cleanly(db):
    result = _run(db, [LLMPermanentError("bad key")])
    assert not result.ok
    assert "permanently unavailable" in result.error


def test_consecutive_retries_are_capped(db):
    result = _run(db, [_answer("no")] * 10)
    assert not result.ok
    assert "consecutive failures" in result.error


def test_a_retry_recovers_and_the_run_still_succeeds(db):
    result = _run(db, [
        LLMTransientError("429"),
        _call(text="ok"),
        _answer("Recovered from the rate limit and completed the goal successfully."),
    ])
    assert result.ok


def test_tool_failure_does_not_kill_the_run(db):
    # The tool is called with a missing required arg -> ToolResult failure ->
    # the agent sees it as an observation and carries on.
    result = _run(db, [
        _call(),                       # `text` missing
        _answer("The tool rejected the arguments, so here is what I can say instead."),
    ])
    assert result.ok
    obs = [s for s in result.trace if s["kind"] == "observation"][0]
    assert obs["output"]["ok"] is False


def test_trace_is_persisted_in_order(db):
    result = _run(db, [_call(text="x"), _answer("Done, and this is a sufficiently long final answer.")])
    idxs = [s["idx"] for s in result.trace]
    assert idxs == sorted(idxs)
    assert len(set(idxs)) == len(idxs)      # (run_id, idx) uniqueness holds


def test_on_step_callback_receives_the_live_trace(db):
    seen = []
    _run(db, [_call(text="x"), _answer("A complete final answer that clears the threshold.")],
         on_step=lambda kind, payload: seen.append(kind))
    assert seen[0] == "goal"
    assert "tool_call" in seen and "observation" in seen and "final" in seen
