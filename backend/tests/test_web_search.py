"""M18 tests. Offline — Tavily is stubbed at the HTTP layer."""

import httpx
import pytest

from app.tools.web_search import (
    MAX_CONTENT_CHARS,
    WebResult,
    WebSearchArgs,
    WebSearchClient,
    WebSearchUnavailable,
    web_search,
)

TAVILY_RESPONSE = {
    "query": "AI internships India",
    "results": [
        {
            "title": "AI Internships 2026",
            "url": "https://example.com/ai-internships",
            "content": "Several Indian startups are hiring ML interns this quarter.",
            "score": 0.93,
        },
        {
            "title": "Internship Guide",
            "url": "https://example.com/guide",
            "content": "How to apply for research internships.",
            "score": 0.71,
        },
    ],
}


def _client_with(handler) -> WebSearchClient:
    c = WebSearchClient(api_key="tvly-test")
    c._client = httpx.Client(transport=httpx.MockTransport(handler))
    return c


# --- happy path -------------------------------------------------------------

def test_maps_tavily_results_to_our_type():
    results = _client_with(lambda r: httpx.Response(200, json=TAVILY_RESPONSE)).search("q")
    assert len(results) == 2
    assert isinstance(results[0], WebResult)
    assert results[0].url == "https://example.com/ai-internships"
    assert results[0].score == 0.93


def test_request_uses_bearer_auth_and_disables_tavilys_own_answer():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"results": []})

    _client_with(handler).search("q", max_results=3)
    assert seen["auth"] == "Bearer tvly-test"
    body = seen["body"].replace(" ", "")
    # include_answer off: Tavily's own LLM summary would be a second model
    # reasoning over the evidence before our agent sees it.
    assert '"include_answer":false' in body
    assert '"include_raw_content":false' in body
    assert '"max_results":3' in body


def test_content_is_truncated_per_result():
    body = {"results": [{"title": "t", "url": "https://e.com", "content": "x" * 5000, "score": 1.0}]}
    results = _client_with(lambda r: httpx.Response(200, json=body)).search("q")
    # A verbose result must not consume the step's token budget — the trace is
    # re-sent on every later turn, so bloat is paid repeatedly.
    assert len(results[0].content) == MAX_CONTENT_CHARS


def test_malformed_result_is_skipped_not_fatal():
    body = {"results": [TAVILY_RESPONSE["results"][0], {"title": "no url"}]}
    assert len(_client_with(lambda r: httpx.Response(200, json=body)).search("q")) == 1


def test_missing_title_falls_back_rather_than_dropping_the_result():
    body = {"results": [{"url": "https://e.com", "content": "c", "score": 0.1}]}
    results = _client_with(lambda r: httpx.Response(200, json=body)).search("q")
    assert results[0].title == "(untitled)"


# --- failures ---------------------------------------------------------------

@pytest.mark.parametrize("status,expected", [(401, "credential"), (403, "credential"),
                                             (429, "quota"), (432, "quota"), (433, "quota")])
def test_auth_and_quota_failures_are_classified(status, expected):
    with pytest.raises(WebSearchUnavailable, match=expected):
        _client_with(lambda r: httpx.Response(status, text="nope")).search("q")


def test_network_error_is_unavailable():
    def handler(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(WebSearchUnavailable, match="unreachable"):
        _client_with(handler).search("q")


def test_unset_api_key_fails_before_any_request():
    c = WebSearchClient(api_key="")
    with pytest.raises(WebSearchUnavailable, match="not configured"):
        c.search("q")


# --- tool wrapper -----------------------------------------------------------

def test_quota_exhaustion_is_unavailable_not_an_empty_web(monkeypatch):
    """M18's Definition of Done: quota exhaustion returns a structured
    observation the agent can replan around, not a crash — and crucially not
    something it could mistake for 'the web has nothing on this'."""
    class Exhausted:
        def search(self, *a, **k):
            raise WebSearchUnavailable("Web search quota exhausted.")

    monkeypatch.setattr("app.tools.web_search.get_client", lambda: Exhausted())
    r = web_search(WebSearchArgs(query="anything"))
    assert r.ok is False
    assert r.unavailable is True
    assert "quota" in r.error.lower()


def test_genuine_empty_result_is_success(monkeypatch):
    class Empty:
        def search(self, *a, **k):
            return []

    monkeypatch.setattr("app.tools.web_search.get_client", lambda: Empty())
    r = web_search(WebSearchArgs(query="zzzz"))
    assert r.ok is True and r.data == [] and r.unavailable is False


def test_success_includes_a_rendered_block(monkeypatch):
    class Fake:
        def search(self, *a, **k):
            return [WebResult(title="T", url="https://e.com", content="C", score=0.5)]

    monkeypatch.setattr("app.tools.web_search.get_client", lambda: Fake())
    r = web_search(WebSearchArgs(query="q"))
    assert "https://e.com" in r.meta["rendered"]


def test_args_are_bounded():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WebSearchArgs(query="")
    with pytest.raises(ValidationError):
        WebSearchArgs(query="q", max_results=50)


# --- registry ---------------------------------------------------------------

def test_registered_and_disambiguated_from_knowledge_search():
    from app.tools import registry

    assert "web_search" in registry.names()
    d = registry.get("web_search").description
    # M0/F7: the description IS the selection algorithm. This one must actively
    # point the model back at knowledge_search for university questions, or the
    # two overlap on every Sitare-related goal.
    assert "knowledge_search" in d
    assert "FIRST" not in d
