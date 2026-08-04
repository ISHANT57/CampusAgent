"""Anthropic adapter tests. No network — every response is a recorded shape
from Anthropic's documented Messages API.

Real Anthropic calls are covered by the Settings page's "Test connection",
which is exactly what surfaced this adapter's absence in the first place.
"""

import httpx
import pytest

from app.llm.anthropic import AnthropicProvider
from app.llm.base import LLMPermanentError, LLMTransientError, Message, ToolSpec

SEARCH_TOOL = ToolSpec(
    name="knowledge_search",
    description="Search the corpus.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
)


def _provider() -> AnthropicProvider:
    return AnthropicProvider(api_key="test-key", model="claude-haiku-4-5-20251001")


# --- request building --------------------------------------------------------

def test_system_message_becomes_a_top_level_field_not_a_message():
    body = _provider()._build_body(
        [Message(role="system", content="You are terse."), Message(role="user", content="hi")],
        None, 0.0,
    )
    assert body["system"] == "You are terse."
    assert len(body["messages"]) == 1          # system is NOT a message turn
    assert body["messages"][0]["role"] == "user"


def test_multiple_system_messages_are_concatenated_not_dropped():
    body = _provider()._build_body(
        [Message(role="system", content="one"), Message(role="system", content="two"),
         Message(role="user", content="q")], None, 0.0,
    )
    assert body["system"] == "one\n\ntwo"


def test_no_system_message_means_no_system_field():
    body = _provider()._build_body([Message(role="user", content="q")], None, 0.0)
    assert "system" not in body


def test_max_tokens_is_always_sent():
    # Required by this API, unlike OpenAI's optional max_tokens.
    body = _provider()._build_body([Message(role="user", content="q")], None, 0.0)
    assert body["max_tokens"] == _provider().max_output_tokens


def test_tools_use_input_schema_untranslated():
    # Unlike Gemini, this format takes JSON Schema as-is.
    body = _provider()._build_body([Message(role="user", content="q")], [SEARCH_TOOL], 0.0)
    tool = body["tools"][0]
    assert tool["name"] == "knowledge_search"
    assert tool["input_schema"] == SEARCH_TOOL.parameters


# --- response parsing ---------------------------------------------------------

def test_parse_tool_call_arguments_are_a_dict_not_a_string():
    # Anthropic returns a real object on `input`. The whole MALFORMED_JSON
    # failure class is structurally impossible on this path, same as Gemini.
    raw = {"content": [
        {"type": "tool_use", "id": "toolu_1", "name": "knowledge_search",
         "input": {"query": "CGPA"}},
    ], "stop_reason": "tool_use"}
    text, calls, finish = AnthropicProvider._parse(raw)
    assert text is None and finish == "tool_use"
    assert calls[0].name == "knowledge_search"
    assert calls[0].arguments == {"query": "CGPA"}
    assert calls[0].id == "toolu_1"


def test_parse_collects_text_and_tool_use_from_the_same_content_list():
    raw = {"content": [
        {"type": "text", "text": "Let me look that up."},
        {"type": "tool_use", "id": "toolu_1", "name": "knowledge_search", "input": {"query": "x"}},
    ], "stop_reason": "tool_use"}
    text, calls, _ = AnthropicProvider._parse(raw)
    assert text == "Let me look that up."
    assert len(calls) == 1


def test_parse_survives_an_empty_response():
    text, calls, finish = AnthropicProvider._parse({})
    assert (text, calls, finish) == (None, [], None)


def test_parse_handles_non_dict_input_without_crashing():
    raw = {"content": [{"type": "tool_use", "name": "calculator", "input": "not an object"}]}
    _, calls, _ = AnthropicProvider._parse(raw)
    assert calls[0].arguments == {}      # rejected, not crashed


# --- error mapping -------------------------------------------------------------

def _resp(status: int, error_type: str, message: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"type": "error", "error": {"type": error_type, "message": message}},
        request=httpx.Request("POST", "https://x"),
    )


def test_rate_limit_error_is_transient():
    with pytest.raises(LLMTransientError):
        _provider()._raise_for_error(_resp(429, "rate_limit_error", "Rate limited"))


def test_overloaded_is_transient():
    # 529 is Anthropic-specific — not a generic HTTP code other adapters see.
    with pytest.raises(LLMTransientError):
        _provider()._raise_for_error(_resp(529, "overloaded_error", "Overloaded"))


def test_authentication_error_is_permanent():
    with pytest.raises(LLMPermanentError):
        _provider()._raise_for_error(_resp(401, "authentication_error", "invalid x-api-key"))


def test_invalid_request_is_permanent():
    with pytest.raises(LLMPermanentError):
        _provider()._raise_for_error(_resp(400, "invalid_request_error", "bad request"))


def test_unrecognised_type_falls_back_to_status_code():
    with pytest.raises(LLMTransientError):
        _provider()._raise_for_error(_resp(503, "", "temporarily unavailable"))
    with pytest.raises(LLMPermanentError):
        _provider()._raise_for_error(_resp(404, "", "not found"))


def test_error_carries_provider_and_model_for_logs():
    with pytest.raises(LLMPermanentError) as e:
        _provider()._raise_for_error(_resp(401, "authentication_error", "bad key"))
    assert e.value.provider == "anthropic"
    assert e.value.model == "claude-haiku-4-5-20251001"
    assert e.value.status == 401


# --- the contract, end to end over a mocked transport --------------------------

def test_complete_sends_the_required_headers_and_parses_usage():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = request.content
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "hi"}],
            "model": "claude-haiku-4-5-20251001",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 12, "output_tokens": 3},
        })

    provider = _provider()
    provider._client = httpx.Client(transport=httpx.MockTransport(handler))

    completion = provider.complete([Message(role="user", content="hi")])

    assert captured["headers"]["x-api-key"] == "test-key"
    assert captured["headers"]["anthropic-version"]
    assert completion.text == "hi"
    assert completion.usage.prompt_tokens == 12
    assert completion.usage.completion_tokens == 3
    assert completion.finish_reason == "end_turn"
