"""M8 tests. No network — every response is a recorded shape from M0.

Real Gemini calls are covered by verify.py; unit tests must stay free,
offline, and deterministic, or they stop being run.
"""

import httpx
import pytest

from app.llm.base import LLMPermanentError, LLMTransientError, Message, ToolSpec
from app.llm.gemini import GeminiProvider, translate_schema

SEARCH_TOOL = ToolSpec(
    name="knowledge_search",
    description="Search the corpus.",
    parameters={
        "type": "object",
        "title": "KnowledgeSearchArgs",          # Pydantic emits this
        "additionalProperties": False,            # Gemini rejects this
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    },
)


# --- schema translation (M0/E6) --------------------------------------------

def test_translate_strips_keys_gemini_rejects():
    out = translate_schema(SEARCH_TOOL.parameters)
    assert "additionalProperties" not in out
    assert "title" not in out
    assert "default" not in out["properties"]["top_k"]
    # ...without destroying the parts Gemini needs
    assert out["properties"]["query"]["type"] == "string"
    assert out["required"] == ["query"]


def test_translate_strips_refs_recursively():
    # The dangerous case: Pydantic emits $ref/$defs for ANY nested model, so
    # without this a nested args model 400s on Gemini.
    nested = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {"Inner": {"type": "object"}},
        "type": "object",
        "properties": {
            "outer": {"type": "object", "title": "drop me", "properties": {"inner": {"type": "string"}}},
            "items": {"type": "array", "items": {"type": "string", "title": "drop me too"}},
            "choice": {"type": "string", "enum": ["a", "b"]},
        },
    }
    out = translate_schema(nested)
    assert "$defs" not in out and "$schema" not in out
    assert "title" not in out["properties"]["outer"]
    assert "title" not in out["properties"]["items"]["items"]
    # M0/E6: enums, arrays and nested objects DO survive — so tool args need
    # translating, not flattening.
    assert out["properties"]["choice"]["enum"] == ["a", "b"]
    assert out["properties"]["outer"]["properties"]["inner"]["type"] == "string"


# --- request building ------------------------------------------------------

def _provider() -> GeminiProvider:
    return GeminiProvider(api_key="test-key", model="gemini-2.5-flash")


def test_system_message_becomes_systemInstruction_not_a_message():
    body = _provider()._build_body(
        [Message(role="system", content="You are terse."), Message(role="user", content="hi")],
        None, 0.0,
    )
    assert body["systemInstruction"]["parts"][0]["text"] == "You are terse."
    assert len(body["contents"]) == 1                      # system is NOT a content turn
    assert body["contents"][0]["role"] == "user"


def test_assistant_role_is_renamed_to_model():
    body = _provider()._build_body(
        [Message(role="user", content="q"), Message(role="assistant", content="a")], None, 0.0
    )
    assert [c["role"] for c in body["contents"]] == ["user", "model"]


def test_multiple_system_messages_are_concatenated_not_dropped():
    body = _provider()._build_body(
        [Message(role="system", content="one"), Message(role="system", content="two"),
         Message(role="user", content="q")], None, 0.0,
    )
    assert body["systemInstruction"]["parts"][0]["text"] == "one\n\ntwo"


def test_tools_become_functionDeclarations():
    body = _provider()._build_body([Message(role="user", content="q")], [SEARCH_TOOL], 0.0)
    decls = body["tools"][0]["functionDeclarations"]
    assert decls[0]["name"] == "knowledge_search"
    assert "additionalProperties" not in decls[0]["parameters"]


# --- response parsing ------------------------------------------------------

def test_parse_tool_call_arguments_are_a_dict_not_a_string():
    # Gemini returns a real object. The whole MALFORMED_JSON failure class is
    # structurally impossible on this path.
    raw = {"candidates": [{"content": {"parts": [
        {"functionCall": {"name": "knowledge_search", "args": {"query": "CGPA", "top_k": 5}}}
    ]}, "finishReason": "STOP"}]}
    text, calls, finish = GeminiProvider._parse(raw)
    assert text is None and finish == "STOP"
    assert calls[0].name == "knowledge_search"
    assert calls[0].arguments == {"query": "CGPA", "top_k": 5}
    assert isinstance(calls[0].arguments, dict)


def test_parse_collects_text_and_calls_from_the_same_parts_list():
    raw = {"candidates": [{"content": {"parts": [
        {"text": "Let me look that up."},
        {"functionCall": {"name": "knowledge_search", "args": {"query": "x"}}},
    ]}, "finishReason": "STOP"}]}
    text, calls, _ = GeminiProvider._parse(raw)
    assert text == "Let me look that up."
    assert len(calls) == 1


def test_parse_survives_an_empty_response():
    text, calls, finish = GeminiProvider._parse({})
    assert (text, calls, finish) == (None, [], None)


def test_parse_handles_non_dict_args_without_crashing():
    raw = {"candidates": [{"content": {"parts": [
        {"functionCall": {"name": "calculator", "args": "6.5 - 6.2"}}
    ]}}]}
    _, calls, _ = GeminiProvider._parse(raw)
    assert calls[0].arguments == {}      # rejected, not crashed


# --- error mapping (M0/F2) -------------------------------------------------

def _resp(status: int, message: str) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"message": message}}, request=httpx.Request("POST", "https://x")
    )


def test_ordinary_429_is_transient():
    with pytest.raises(LLMTransientError):
        _provider()._raise_for_error(_resp(429, "Resource exhausted, please retry in 52s"))


def test_limit_zero_429_is_permanent():
    # THE M0/F2 case. Same status code as above, opposite correct response:
    # this model's free tier is retired, so retrying can never succeed and
    # would burn the run's whole budget.
    with pytest.raises(LLMPermanentError):
        _provider()._raise_for_error(
            _resp(429, "Quota exceeded ... generate_content_free_tier_requests, limit: 0")
        )


def test_retired_model_404_is_permanent():
    with pytest.raises(LLMPermanentError):
        _provider()._raise_for_error(_resp(404, "This model is no longer available to new users."))


def test_bad_api_key_is_permanent():
    with pytest.raises(LLMPermanentError):
        _provider()._raise_for_error(_resp(400, "API key not valid. Please pass a valid API key."))


def test_server_error_is_transient():
    with pytest.raises(LLMTransientError):
        _provider()._raise_for_error(_resp(503, "The service is currently unavailable."))


def test_error_carries_provider_and_model_for_logs():
    with pytest.raises(LLMPermanentError) as e:
        _provider()._raise_for_error(_resp(400, "API key not valid"))
    assert e.value.provider == "gemini"
    assert e.value.model == "gemini-2.5-flash"
    assert e.value.status == 400
