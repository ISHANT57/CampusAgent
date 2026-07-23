"""M12 + M13 tests: the registry contract and the no-tool-may-raise guarantee."""

import time

import pytest
from pydantic import BaseModel, Field

from app.tools.base import ToolResult
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry


class EchoArgs(BaseModel):
    text: str = Field(description="anything")
    times: int = Field(default=1, description="how many times")


def _fresh() -> tuple[ToolRegistry, ToolExecutor]:
    r = ToolRegistry()
    return r, ToolExecutor(r)


# --- M12 registry -----------------------------------------------------------

def test_schema_is_derived_from_the_args_model():
    r, _ = _fresh()

    @r.register(description="Echo the text back.")
    def echo(args: EchoArgs) -> ToolResult:
        return ToolResult.success(args.text)

    spec = r.specs()[0]
    assert spec.name == "echo"
    assert spec.parameters["properties"]["text"]["type"] == "string"
    assert spec.parameters["required"] == ["text"]      # `times` has a default


def test_rejects_ordering_language_in_descriptions():
    # M0/F7 encoded as a guard. "Use this FIRST..." caused every wrong tool
    # choice in the spike; the registry now refuses to accept it.
    r, _ = _fresh()
    with pytest.raises(ValueError, match="ordering language"):

        @r.register(description="Use this FIRST when you need to list things.")
        def lister(args: EchoArgs) -> ToolResult:
            return ToolResult.success(None)


def test_rejects_a_tool_without_a_pydantic_args_model():
    r, _ = _fresh()
    with pytest.raises(TypeError):

        @r.register(description="Bad tool.")
        def bad(args: dict) -> ToolResult:
            return ToolResult.success(None)


def test_rejects_duplicate_names():
    r, _ = _fresh()

    @r.register(description="One.")
    def dupe(args: EchoArgs) -> ToolResult:
        return ToolResult.success(None)

    with pytest.raises(ValueError, match="duplicate"):

        @r.register(description="Two.", name="dupe")
        def other(args: EchoArgs) -> ToolResult:
            return ToolResult.success(None)


# --- M13 executor: no tool may raise ----------------------------------------

def test_unknown_tool_is_a_result_not_an_exception():
    _, ex = _fresh()
    r = ex.execute("nope", {})
    assert r.ok is False and "Unknown tool" in r.error


def test_invalid_arguments_return_a_repairable_message():
    r_, ex = _fresh()

    @r_.register(description="Echo the text back.")
    def echo(args: EchoArgs) -> ToolResult:
        return ToolResult.success(args.text)

    res = ex.execute("echo", {})                  # `text` is required
    assert res.ok is False
    # The detail IS the repair instruction — it tells the model what to fix.
    assert "text" in res.error and "required" in res.error.lower()


def test_a_raising_tool_becomes_a_failure_result():
    r_, ex = _fresh()

    @r_.register(description="Always explodes.")
    def boom(args: EchoArgs) -> ToolResult:
        raise RuntimeError("kaboom")

    res = ex.execute("boom", {"text": "x"})
    assert res.ok is False
    assert "RuntimeError" in res.error and "kaboom" in res.error


def test_a_hanging_tool_times_out_as_unavailable():
    r_, ex = _fresh()

    @r_.register(description="Sleeps forever.", timeout_s=0.2)
    def sleeper(args: EchoArgs) -> ToolResult:
        time.sleep(5)
        return ToolResult.success("never")

    res = ex.execute("sleeper", {"text": "x"})
    assert res.ok is False
    # unavailable, NOT failed: the tool may be fine, it was just slow. The
    # agent's correct response differs — M0/F9.
    assert res.unavailable is True
    assert "timed out" in res.error


def test_a_tool_returning_the_wrong_type_is_caught():
    r_, ex = _fresh()

    @r_.register(description="Returns a bare string.")
    def sloppy(args: EchoArgs) -> ToolResult:
        return "not a ToolResult"  # type: ignore[return-value]

    res = ex.execute("sloppy", {"text": "x"})
    assert res.ok is False and "expected ToolResult" in res.error


# --- M13 caching ------------------------------------------------------------

def test_identical_calls_are_served_from_cache_within_a_run():
    r_, ex = _fresh()
    calls = {"n": 0}

    @r_.register(description="Counts invocations.")
    def counter(args: EchoArgs) -> ToolResult:
        calls["n"] += 1
        return ToolResult.success(calls["n"])

    first = ex.execute("counter", {"text": "same"})
    second = ex.execute("counter", {"text": "same"})

    assert calls["n"] == 1                       # the function ran once
    assert second.data == first.data
    assert second.meta.get("cached") is True
    assert first.meta.get("cached") is None      # the original is unmarked


def test_different_arguments_are_not_cached_together():
    r_, ex = _fresh()
    calls = {"n": 0}

    @r_.register(description="Counts invocations.")
    def counter(args: EchoArgs) -> ToolResult:
        calls["n"] += 1
        return ToolResult.success(calls["n"])

    ex.execute("counter", {"text": "a"})
    ex.execute("counter", {"text": "b"})
    assert calls["n"] == 2


def test_failures_are_not_cached():
    # A transient failure must not poison the rest of the run.
    r_, ex = _fresh()
    attempts = {"n": 0}

    @r_.register(description="Fails once, then works.")
    def flaky(args: EchoArgs) -> ToolResult:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return ToolResult.down("temporarily unavailable")
        return ToolResult.success("recovered")

    assert ex.execute("flaky", {"text": "x"}).ok is False
    assert ex.execute("flaky", {"text": "x"}).ok is True


def test_reset_clears_the_cache_between_runs():
    r_, ex = _fresh()
    calls = {"n": 0}

    @r_.register(description="Counts invocations.")
    def counter(args: EchoArgs) -> ToolResult:
        calls["n"] += 1
        return ToolResult.success(calls["n"])

    ex.execute("counter", {"text": "x"})
    ex.reset()
    ex.execute("counter", {"text": "x"})
    assert calls["n"] == 2


# --- the real registry ------------------------------------------------------

def test_builtin_registry_contents():
    from app.tools import registry

    # The MVP vocabulary. No final_answer: the loop recognises completion when
    # the model stops requesting tools (see app/tools/__init__.py).
    assert sorted(registry.names()) == [
        "calculator",
        "knowledge_list_documents",
        "knowledge_read_document",
        "knowledge_search",
        "web_read",
        "web_search",
    ]


def test_builtin_specs_are_gemini_translatable():
    from app.llm.gemini import translate_schema
    from app.tools import registry

    for spec in registry.specs():
        out = translate_schema(spec.parameters)
        assert "$defs" not in out and "additionalProperties" not in out
        assert out["properties"]
