"""M39 + M41 tests. Offline — no LLM, no database, no network.

The scoring logic is pure functions over recorded traces, which is what makes
this suite free to run. `cli.py eval` runs the real agent when you want a real
number; these tests prove the arithmetic behind it is right.
"""

import pytest

from app.eval.metrics import (
    GoalScore,
    Report,
    load_golden,
    runnable_goals,
    score_goal,
)
from app.models.run import RunStatus


class FakeResult:
    """Stands in for a RunResult."""

    def __init__(self, trace, answer=None, status=RunStatus.COMPLETED, steps=None):
        self.trace = trace
        self.answer = answer
        self.status = status
        self.steps = steps if steps is not None else len(trace)
        self.prompt_tokens = 100
        self.completion_tokens = 20
        self.elapsed_seconds = 3.0
        self.error = None

    @property
    def ok(self):
        return self.status is RunStatus.COMPLETED


def _trace(*tools, unavailable=()):
    out = []
    for i, t in enumerate(tools):
        out.append({"idx": len(out), "kind": "tool_call", "tool": t, "input": {}, "output": None, "error": None})
        out.append({
            "idx": len(out), "kind": "observation", "tool": t,
            "input": None, "output": {"ok": t not in unavailable, "unavailable": t in unavailable},
            "error": None,
        })
    out.append({"idx": len(out), "kind": "final", "tool": None, "input": None, "output": {}, "error": None})
    return out


# --- M39: the golden set is well-formed -------------------------------------

def test_golden_set_loads_and_has_twelve_goals():
    goals = load_golden()
    assert len(goals) == 12
    assert [g["id"] for g in goals] == [f"G{i:02d}" for i in range(1, 13)]


def test_every_goal_declares_what_it_needs():
    for g in load_golden():
        assert g["goal"].strip()
        assert "expected_tool" in g          # may be null: G09 needs no tool
        assert isinstance(g.get("requires", []), list)
        assert g["min_steps"] >= 1
        assert g["why"].strip(), f"{g['id']} has no rationale"


def test_acceptable_tools_never_duplicate_the_expected_one():
    for g in load_golden():
        assert g["expected_tool"] not in g.get("acceptable_tools", [])


def test_goals_needing_unregistered_tools_are_skipped_not_failed():
    """Scoring G02 as a failure because knowledge_list_documents does not exist
    yet would depress the success rate for a reason unrelated to the agent —
    and hide real regressions behind a known-bad number."""
    runnable, skipped = runnable_goals({"knowledge_search", "calculator", "web_search"})
    skipped_ids = {g["id"] for g in skipped}
    assert skipped_ids == {"G02", "G08", "G11"}          # need M17 tools / web_read
    assert all(g["missing_tools"] for g in skipped)
    assert len(runnable) == 9


def test_all_goals_runnable_once_every_tool_exists():
    runnable, skipped = runnable_goals({
        "knowledge_search", "knowledge_list_documents", "knowledge_read_document",
        "calculator", "web_search", "web_read",
    })
    assert len(runnable) == 12 and not skipped


# --- M41: scoring -----------------------------------------------------------

def test_correct_first_tool_scores():
    goal = {"id": "G01", "expected_tool": "knowledge_search", "min_steps": 3, "answer_contains": ["6.5"]}
    s = score_goal(goal, FakeResult(_trace("knowledge_search"), answer="The minimum is 6.5 CGPA."))
    assert s.tool_correct is True
    assert s.answer_ok is True
    assert s.completed is True


def test_wrong_first_tool_is_caught_even_when_the_run_succeeds():
    # The trajectory matters, not just the answer.
    goal = {"id": "G01", "expected_tool": "knowledge_search", "min_steps": 3, "answer_contains": []}
    s = score_goal(goal, FakeResult(_trace("web_search"), answer="Some answer that is long enough."))
    assert s.tool_correct is False
    assert s.completed is True


def test_acceptable_alternative_counts_as_correct():
    goal = {"id": "G07", "expected_tool": "knowledge_search",
            "acceptable_tools": ["web_search"], "min_steps": 5, "answer_contains": []}
    s = score_goal(goal, FakeResult(_trace("web_search"), answer="x" * 50))
    assert s.tool_correct is True


def test_no_tool_expected_and_none_used():
    goal = {"id": "G09", "expected_tool": None, "min_steps": 1, "answer_contains": []}
    s = score_goal(goal, FakeResult([{"idx": 0, "kind": "final", "tool": None, "output": {}, "error": None}],
                                    answer="I can help with policies and calculations."))
    assert s.tool_correct is True


def test_no_tool_expected_but_one_was_used_is_over_triggering():
    goal = {"id": "G09", "expected_tool": None, "min_steps": 1, "answer_contains": []}
    s = score_goal(goal, FakeResult(_trace("knowledge_search"), answer="x" * 50))
    assert s.tool_correct is False


def test_a_run_that_died_before_choosing_is_unscored_not_correct():
    """REGRESSION, found on the first live eval.

    G09's correct move is NO tool. A run that 429'd before doing anything also
    has no tool call, so it scored as a correct decision — an infrastructure
    outage inflating the selection score. A metric that improves when the
    provider goes down is worse than no metric.
    """
    goal = {"id": "G09", "expected_tool": None, "min_steps": 1, "answer_contains": []}
    dead = FakeResult([], answer=None, status=RunStatus.FAILED, steps=0)
    assert score_goal(goal, dead).tool_correct is None

    # The same guard must not swallow a genuine no-tool success.
    alive = FakeResult([{"idx": 0, "kind": "final", "tool": None, "output": {}, "error": None}],
                       answer="I can help with policies and calculations.")
    assert score_goal(goal, alive).tool_correct is True


def test_a_failed_run_that_did_choose_a_tool_is_still_scored():
    # Evidence exists here: it picked something before failing.
    goal = {"id": "G01", "expected_tool": "knowledge_search", "min_steps": 3, "answer_contains": []}
    failed = FakeResult(_trace("web_search"), answer=None, status=RunStatus.FAILED)
    assert score_goal(goal, failed).tool_correct is False


def test_answer_assertion_fails_when_the_fact_is_missing():
    goal = {"id": "G01", "expected_tool": "knowledge_search", "min_steps": 3, "answer_contains": ["6.5"]}
    s = score_goal(goal, FakeResult(_trace("knowledge_search"), answer="I could not determine that."))
    assert s.answer_ok is False


def test_answer_accuracy_is_not_scored_when_nothing_was_asserted():
    goal = {"id": "G06", "expected_tool": "web_search", "min_steps": 3, "answer_contains": []}
    s = score_goal(goal, FakeResult(_trace("web_search"), answer="anything"))
    assert s.answer_ok is None


def test_unavailable_tools_are_counted_separately():
    """A low success rate caused by a dependency outage is a different problem
    from one caused by bad reasoning. Averaging them hides both."""
    goal = {"id": "G01", "expected_tool": "knowledge_search", "min_steps": 3, "answer_contains": []}
    s = score_goal(
        goal,
        FakeResult(_trace("knowledge_search", "web_search", unavailable={"knowledge_search"}),
                   answer="x" * 50),
    )
    assert s.tools_unavailable == 1


def test_step_efficiency():
    goal = {"id": "G01", "expected_tool": "knowledge_search", "min_steps": 3, "answer_contains": []}
    s = score_goal(goal, FakeResult(_trace("knowledge_search"), answer="x" * 50, steps=3))
    assert s.efficiency == 1.0
    s2 = score_goal(goal, FakeResult(_trace("knowledge_search"), answer="x" * 50, steps=6))
    assert s2.efficiency == 0.5


# --- M41: aggregation -------------------------------------------------------

def _score(**kw):
    base = dict(goal_id="G", completed=True, first_tool="t", expected_tool="t",
                tool_correct=True, steps=3, min_steps=3, answer_ok=None,
                tools_unavailable=0, tokens=100, seconds=1.0)
    return GoalScore(**{**base, **kw})


def test_grounded_when_the_fact_came_from_an_observation():
    goal = {"id": "G01", "expected_tool": "knowledge_search", "min_steps": 3, "answer_contains": ["6.5"]}
    trace = _trace("knowledge_search")
    trace[1]["output"]["data"] = [{"text": "maintain CGPA >= 6.5 to retain the scholarship"}]
    s = score_goal(goal, FakeResult(trace, answer="You need a CGPA of 6.5 [1]."))
    assert s.grounded is True


def test_ungrounded_when_the_fact_was_never_retrieved():
    """The gap `answer_contains` alone cannot see.

    The model states 6.5 and it happens to be right — but no tool ever returned
    it, so it was recalled from pretraining, not retrieved. Right today, and
    silently wrong the moment the policy changes.
    """
    goal = {"id": "G01", "expected_tool": "knowledge_search", "min_steps": 3, "answer_contains": ["6.5"]}
    trace = _trace("knowledge_search")
    trace[1]["output"]["data"] = [{"text": "hostel timings are 6am to 9pm"}]
    s = score_goal(goal, FakeResult(trace, answer="You need a CGPA of 6.5 [1]."))
    assert s.answer_ok is True          # the shallow check still passes...
    assert s.grounded is False          # ...but groundedness catches it
    assert "never retrieved" in s.grounded_detail


def test_numeric_facts_match_tolerantly_not_by_substring():
    """REGRESSION from the first full eval.

    calculator returns 0.2999999999999998; the agent correctly reports "0.3".
    A plain substring check called that ungrounded — flagging correct behaviour,
    which trains you to ignore the metric.
    """
    goal = {"id": "G04", "expected_tool": "calculator", "min_steps": 3, "answer_contains": ["0.3"]}
    trace = _trace("calculator")
    trace[1]["output"]["data"] = 0.2999999999999998
    assert score_goal(goal, FakeResult(trace, answer="The difference is 0.3.")).grounded is True


def test_calculator_only_answers_need_no_citation():
    """Second false positive from the same check.

    "What is 6.5 minus 6.2?" has no source to cite. Demanding "[1]" would mark
    a perfectly grounded arithmetic answer as ungrounded.
    """
    goal = {"id": "G04", "expected_tool": "calculator", "min_steps": 3, "answer_contains": ["0.3"]}
    trace = _trace("calculator")
    trace[1]["output"]["data"] = 0.3
    s = score_goal(goal, FakeResult(trace, answer="6.5 minus 6.2 is 0.3."))
    assert s.grounded is True


def test_ungrounded_when_the_answer_carries_no_citation():
    goal = {"id": "G01", "expected_tool": "knowledge_search", "min_steps": 3, "answer_contains": ["6.5"]}
    trace = _trace("knowledge_search")
    trace[1]["output"]["data"] = [{"text": "CGPA >= 6.5"}]
    s = score_goal(goal, FakeResult(trace, answer="You need a CGPA of 6.5."))
    assert s.grounded is False
    assert "citation" in s.grounded_detail


def test_multi_number_citations_are_recognised():
    goal = {"id": "G01", "expected_tool": "knowledge_search", "min_steps": 3, "answer_contains": ["6.5"]}
    trace = _trace("knowledge_search")
    trace[1]["output"]["data"] = [{"text": "CGPA >= 6.5"}]
    s = score_goal(goal, FakeResult(trace, answer="A CGPA of 6.5 is required [1, 2, 3]."))
    assert s.grounded is True


def test_groundedness_is_unscored_without_assertions_or_observations():
    goal = {"id": "G06", "expected_tool": "web_search", "min_steps": 3, "answer_contains": []}
    assert score_goal(goal, FakeResult(_trace("web_search"), answer="x" * 50)).grounded is None

    goal2 = {"id": "G04", "expected_tool": "calculator", "min_steps": 3, "answer_contains": ["0.3"]}
    no_obs = [{"idx": 0, "kind": "final", "tool": None, "output": {}, "error": None}]
    assert score_goal(goal2, FakeResult(no_obs, answer="0.3")).grounded is None


def test_report_rates():
    r = Report(scores=[
        _score(completed=True, tool_correct=True, answer_ok=True),
        _score(completed=True, tool_correct=False, answer_ok=False),
        _score(completed=False, tool_correct=True, answer_ok=None),
    ])
    d = r.as_dict()
    assert d["success_rate"].startswith("2/3")
    assert d["tool_selection_accuracy"].startswith("2/3")
    # Only the two goals that asserted something are counted.
    assert d["answer_accuracy"].startswith("1/2")


def test_report_handles_an_empty_run():
    assert Report().as_dict()["success_rate"] == "n/a"


def test_degraded_runs_counted():
    r = Report(scores=[_score(tools_unavailable=1), _score(), _score(tools_unavailable=2)])
    assert r.as_dict()["degraded_runs"] == 2


def test_unscored_tool_selection_is_excluded_not_counted_as_wrong():
    r = Report(scores=[_score(tool_correct=True), _score(tool_correct=None)])
    assert r.as_dict()["tool_selection_accuracy"].startswith("1/1")
