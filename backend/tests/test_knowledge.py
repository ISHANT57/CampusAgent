"""M15 tests. No network — Project 1 is stubbed at the HTTP layer.

The live integration is verified by verify.py against the deployed service.
Unit tests must stay offline and free, or they stop being run.
"""

import httpx
import pytest

from app.tools.base import ToolResult
from app.tools.knowledge import (
    KnowledgeClient,
    KnowledgeUnavailable,
    Passage,
    knowledge_search,
    KnowledgeSearchArgs,
)

# A real Project 1 response shape (app/schemas/search.py: SearchResponse).
P1_RESPONSE = {
    "hits": [
        {
            "score": 0.81,
            "chunk_id": 42,
            "document_id": 3,
            "page_number": 2,
            "text": "To retain the scholarship each year: maintain CGPA >= 6.5.",
        },
        {
            "score": 0.64,
            "chunk_id": 43,
            "document_id": 3,
            "page_number": 2,
            "text": "At least 90% attendance per course is also required.",
        },
    ]
}


def _client_with(handler) -> KnowledgeClient:
    c = KnowledgeClient(base_url="https://p1.test", api_key="k", timeout=5)
    c._client = httpx.Client(transport=httpx.MockTransport(handler))
    return c


# --- the happy path ---------------------------------------------------------

def test_search_maps_p1_hits_to_our_passages():
    c = _client_with(lambda r: httpx.Response(200, json=P1_RESPONSE))
    passages = c.search("scholarship CGPA")

    assert len(passages) == 2
    assert isinstance(passages[0], Passage)      # OUR type, not P1's
    assert passages[0].document_id == 3
    assert passages[0].chunk_id == 42
    assert "6.5" in passages[0].text


def test_request_carries_the_api_key_and_hits_the_right_path():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("X-API-Key")
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"hits": []})

    _client_with(handler).search("q", top_k=7)
    assert seen["url"] == "https://p1.test/api/v1/search"
    assert seen["key"] == "k"
    assert '"top_k":7' in seen["body"].replace(" ", "")


def test_trailing_slash_in_base_url_does_not_double_up():
    c = KnowledgeClient(base_url="https://p1.test/", api_key="k")
    assert c.base_url == "https://p1.test"


def test_passage_render_carries_attribution_into_the_prompt():
    p = Passage(text="CGPA >= 6.5", document_id=3, page_number=2, score=0.81, chunk_id=42)
    out = p.render(1)
    assert out.startswith("[1] (document 3, page 2, score 0.81)")
    assert "CGPA >= 6.5" in out


# --- failures -----------------------------------------------------------

def test_bad_credential_fails_immediately_with_an_actionable_message():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, json={"detail": "Invalid API key"})

    with pytest.raises(KnowledgeUnavailable, match="KNOWLEDGE_BASE_API_KEY"):
        _client_with(handler).search("q")
    # No retry: a second identical request cannot fix a wrong key, and retrying
    # would turn a clear config error into a vague timeout.
    assert calls["n"] == 1


def test_server_error_is_retried_once():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, text="upstream down")

    with pytest.raises(KnowledgeUnavailable):
        _client_with(handler).search("q")
    assert calls["n"] == 2


def test_cold_start_timeout_is_retried_and_can_succeed():
    # Project 1 is on Render free tier: the first request after idle can time
    # out while the instance wakes. The retry is what makes the first search of
    # a session work.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("cold start")
        return httpx.Response(200, json=P1_RESPONSE)

    passages = _client_with(handler).search("q")
    assert len(passages) == 2 and calls["n"] == 2


def test_client_error_is_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(422, json={"detail": "bad query"})

    with pytest.raises(KnowledgeUnavailable):
        _client_with(handler).search("q")
    assert calls["n"] == 1


def test_malformed_hit_is_skipped_not_fatal():
    # One bad row from P1 degrades the result set; it does not fail the search.
    body = {"hits": [P1_RESPONSE["hits"][0], {"score": 0.5, "text": "missing ids"}]}
    passages = _client_with(lambda r: httpx.Response(200, json=body)).search("q")
    assert len(passages) == 1


# --- the tool wrapper -------------------------------------------------------

def test_tool_reports_outage_as_unavailable_not_as_empty_corpus(monkeypatch):
    """THE distinction this milestone turns on.

    'The knowledge base is down' must never be reasoned about as 'the corpus
    does not contain this' — that produces a confidently wrong answer instead
    of an honest one. Same lesson as M0/F9.
    """
    class Down:
        def search(self, *a, **k):
            raise KnowledgeUnavailable("service unreachable")

    monkeypatch.setattr("app.tools.knowledge.get_client", lambda: Down())
    r = knowledge_search(KnowledgeSearchArgs(query="anything"))
    assert r.ok is False
    assert r.unavailable is True


def test_tool_reports_a_genuine_empty_result_as_success(monkeypatch):
    # P1 answered and found nothing. That IS evidence, so ok=True — the agent
    # can honestly say the corpus does not cover it.
    class Empty:
        def search(self, *a, **k):
            return []

    monkeypatch.setattr("app.tools.knowledge.get_client", lambda: Empty())
    r = knowledge_search(KnowledgeSearchArgs(query="anything"))
    assert r.ok is True and r.data == [] and r.meta["count"] == 0
    assert r.unavailable is False


def test_tool_success_includes_a_rendered_block_for_the_prompt(monkeypatch):
    class Fake:
        def search(self, *a, **k):
            return [Passage(text="CGPA >= 6.5", document_id=3, page_number=2, score=0.81, chunk_id=42)]

    monkeypatch.setattr("app.tools.knowledge.get_client", lambda: Fake())
    r = knowledge_search(KnowledgeSearchArgs(query="cgpa"))
    assert r.ok is True
    assert "document 3, page 2" in r.meta["rendered"]


def test_args_are_bounded():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        KnowledgeSearchArgs(query="")            # empty
    with pytest.raises(ValidationError):
        KnowledgeSearchArgs(query="q", top_k=99)  # unbounded retrieval


# --- registry integration ---------------------------------------------------

def test_tool_is_registered_and_gemini_translatable():
    from app.llm.gemini import translate_schema
    from app.tools import registry

    assert "knowledge_search" in registry.names()
    spec = next(s for s in registry.specs() if s.name == "knowledge_search")
    out = translate_schema(spec.parameters)
    assert "$defs" not in out
    assert out["properties"]["query"]["type"] == "string"


def test_description_states_what_it_returns():
    # M0/F7: the description IS the selection algorithm. "Returns short
    # fragments, not whole documents" is what stops the model reaching for
    # this tool on a whole-document summarisation task.
    from app.tools import registry

    d = registry.get("knowledge_search").description
    assert "fragments" in d.lower()
    assert "FIRST" not in d
