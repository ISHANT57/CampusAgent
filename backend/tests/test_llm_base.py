"""parse_json_response — shared across all three adapters.

Regression coverage for a real bug: a custom/Ollama base URL missing its
`/v1` suffix (or any misrouted endpoint) can 200 with an empty or HTML body.
`response.json()` then raised a bare json.JSONDecodeError that was not an
LLMError, escaped every adapter's error handling, and reached a user as
"JSONDecodeError: Expecting value: line 1 column 1 (char 0)" — true, and
useless to someone who does not know what raised it.
"""

import httpx
import pytest

from app.llm.base import LLMPermanentError, parse_json_response


def _resp(status: int, body: str) -> httpx.Response:
    return httpx.Response(status, content=body, request=httpx.Request("POST", "https://x"))


def test_valid_json_passes_through():
    resp = httpx.Response(200, json={"a": 1}, request=httpx.Request("POST", "https://x"))
    assert parse_json_response(resp, provider="p", model="m") == {"a": 1}


def test_empty_200_body_is_a_permanent_error_not_a_bare_json_error():
    # THE bug: char 0 is exactly the "Expecting value: line 1 column 1 (char 0)"
    # a user saw when a custom endpoint's base URL was missing /v1.
    with pytest.raises(LLMPermanentError) as e:
        parse_json_response(_resp(200, ""), provider="custom", model="claude-opus-4-8")
    assert "base URL" in str(e.value)
    assert e.value.provider == "custom"
    assert e.value.status == 200


def test_html_200_body_is_also_caught():
    # A misrouted request can 200 an HTML page just as easily as an empty one.
    with pytest.raises(LLMPermanentError):
        parse_json_response(_resp(200, "<html><body>Not Found</body></html>"), provider="p", model="m")
