"""The ONLY module that knows Project 1 exists.

Everything about the Knowledge Service — its base URL, its credential, its
response shape, its failure modes — is contained here. Swapping Project 1 for
any other retrieval provider means rewriting this file and nothing else.

That containment is the point of the whole two-repo architecture, and it is
enforced by one rule: **P1's types never cross this boundary.** P1 returns a
`SearchHit`; the agent consumes a `Passage`, which is ours. If P1 renames a
field, one mapping function changes and no other file notices.

The agent's entire view of the knowledge base is:

    knowledge_search(query, top_k) -> passages

It does not know that behind that call sit PyMuPDF, PaddleOCR, recursive
chunking, Gemini embeddings, Qdrant, Postgres full-text search, and Reciprocal
Rank Fusion. If Project 1 replaced every one of those tomorrow, this file would
not change.
"""

from __future__ import annotations

import time
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.tools.base import ToolResult
from app.tools.registry import registry


class Passage(BaseModel):
    """A retrieved fragment. P2's OWN type, mapped from P1's SearchHit.

    Deliberately not an import of P1's schema: importing it would weld the two
    repositories together and make P1's internal refactors into P2's breaking
    changes.
    """

    text: str
    document_id: int
    page_number: int
    score: float
    chunk_id: int

    def render(self, index: int) -> str:
        """How this passage appears in a prompt. Attribution travels WITH the
        text so the model can cite it, and so a later answer can be traced back
        to a document and page."""
        return f"[{index}] (document {self.document_id}, page {self.page_number}, score {self.score:.2f})\n{self.text}"


class KnowledgeUnavailable(Exception):
    """P1 could not be reached or refused us. Distinct from 'P1 answered and
    found nothing' — the agent must not conclude the corpus lacks an answer
    when the corpus was never actually consulted."""


class KnowledgeClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float | None = None):
        s = get_settings()
        # rstrip: a trailing slash in configuration would produce
        # `//api/v1/search`, which some proxies redirect and others 404.
        self.base_url = (base_url or s.knowledge_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else s.knowledge_base_api_key
        self.timeout = timeout or s.knowledge_timeout
        self._client = httpx.Client(timeout=self.timeout)

    def search(
        self,
        query: str,
        top_k: int = 5,
        mode: Literal["semantic", "keyword", "hybrid"] = "hybrid",
    ) -> list[Passage]:
        """POST /api/v1/search. Raises KnowledgeUnavailable on any failure.

        One retry, because Project 1 runs on Render's free tier: the first
        request after idle hits a cold start, and a single attempt would make
        the first search of every session fail. The retry is NOT for correctness
        — a second identical request cannot fix a 400.
        """
        payload = {"query": query, "top_k": top_k, "mode": mode}
        last_error = ""

        for attempt in range(2):
            try:
                response = self._client.post(
                    f"{self.base_url}/api/v1/search",
                    headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
                    json=payload,
                )
            except httpx.HTTPError as e:
                last_error = f"{type(e).__name__}: {e}"
                if attempt == 0:
                    time.sleep(2)      # cold start; give it one more chance
                    continue
                raise KnowledgeUnavailable(f"Knowledge service unreachable ({last_error})") from e

            if response.status_code == 200:
                return self._to_passages(response.json())

            # 401/403 mean the credential is wrong — a deployment problem the
            # agent cannot solve by rephrasing. Fail immediately and loudly
            # rather than burning a retry and then reporting a vague timeout.
            if response.status_code in (401, 403):
                raise KnowledgeUnavailable(
                    f"Knowledge service rejected our credential ({response.status_code}). "
                    "Check KNOWLEDGE_BASE_API_KEY matches Project 1's SERVICE_API_KEY."
                )
            if response.status_code < 500 and response.status_code != 429:
                raise KnowledgeUnavailable(
                    f"Knowledge service returned {response.status_code}: {response.text[:200]}"
                )

            last_error = f"HTTP {response.status_code}"
            if attempt == 0:
                time.sleep(2)

        raise KnowledgeUnavailable(f"Knowledge service failing ({last_error})")

    @staticmethod
    def _to_passages(body: dict[str, Any]) -> list[Passage]:
        """P1's SearchHit -> our Passage. The anti-corruption boundary.

        Tolerant of missing keys rather than strict: a partially-shaped hit is
        skipped, so one malformed row from P1 degrades the result set instead of
        failing the whole search — and the agent still gets something to reason
        with.
        """
        passages: list[Passage] = []
        for hit in body.get("hits", []):
            try:
                passages.append(
                    Passage(
                        text=hit["text"],
                        document_id=hit["document_id"],
                        page_number=hit["page_number"],
                        score=hit["score"],
                        chunk_id=hit["chunk_id"],
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return passages


_client: KnowledgeClient | None = None


def get_client() -> KnowledgeClient:
    """Lazy singleton. Built on first use, not at import, so the module can be
    imported (and its tool registered) without valid credentials — which is
    what lets the test suite run offline."""
    global _client
    if _client is None:
        _client = KnowledgeClient()
    return _client


class KnowledgeSearchArgs(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=500,
        description="The search query. Use specific terms drawn from the question.",
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        description="How many passages to return. Default 5.",
    )


@registry.register(
    description=(
        "Search the university's internal document corpus and return the most "
        "relevant passages, each with its source document, page number, and a "
        "relevance score. Use this for questions about official university "
        "policies, rules, curriculum, admissions, fees, placements, scholarships, "
        "or campus information. Returns short fragments, not whole documents."
    ),
    timeout_s=65.0,  # must exceed the client's 30s + retry + backoff
)
def knowledge_search(args: KnowledgeSearchArgs) -> ToolResult:
    try:
        passages = get_client().search(args.query, top_k=args.top_k)
    except KnowledgeUnavailable as e:
        # `unavailable`, not `failure`. The distinction matters to the agent:
        # "the knowledge base is down" must not be reasoned about as "the
        # corpus does not contain this", which would produce a confidently
        # wrong answer instead of an honest one.
        return ToolResult.down(str(e))

    if not passages:
        # A successful search that found nothing. This IS evidence — the corpus
        # was consulted and does not cover the question — so it is ok=True with
        # an empty result, letting the agent try a different query or say so.
        return ToolResult.success([], count=0, query=args.query)

    return ToolResult.success(
        [p.model_dump() for p in passages],
        count=len(passages),
        query=args.query,
        rendered="\n\n".join(p.render(i) for i, p in enumerate(passages, start=1)),
    )
