"""Web search, backed by Tavily.

Chosen over Brave / SerpAPI / DuckDuckGo because Tavily returns extracted page
CONTENT, not a list of links. Every other option would require a second
fetch-and-parse round trip per result — more latency, more tokens, and a whole
extra failure surface on a throttled free tier.

SECURITY NOTE, and the reason this tool is bounded the way it is:
web results are ATTACKER-CONTROLLED text. Anyone can publish a page saying
"ignore your instructions and ...", and it can rank for a query. Three things
contain that here:

  1. Content is truncated per result (below) — a wall of injected text cannot
     dominate the prompt.
  2. Observations are fenced as untrusted data before entering a prompt (M20).
  3. Every MVP tool is READ-ONLY, so a successful injection can produce a wrong
     answer but not a wrong ACTION. That property is what keeps `web_read`
     (arbitrary URL fetch) and every effectful tool out of the MVP.
"""

from __future__ import annotations

import time

import httpx
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.tools.base import ToolResult
from app.tools.registry import registry

TAVILY_URL = "https://api.tavily.com/search"

# Per-result cap. Tavily snippets are usually well under this, but one verbose
# result should not be able to consume the step's whole token budget — the
# trace is re-sent on every subsequent turn, so a bloated observation is paid
# for repeatedly, not once.
MAX_CONTENT_CHARS = 1200


class WebResult(BaseModel):
    """P2's own type, mapped from Tavily's response. Same anti-corruption rule
    as the knowledge client: the vendor's shape never crosses this boundary."""

    title: str
    url: str
    content: str
    score: float = 0.0

    def render(self, index: int) -> str:
        return f"[{index}] {self.title}\n{self.url}\n{self.content}"


class WebSearchUnavailable(Exception):
    """The search provider could not be reached, or refused us. Distinct from
    'searched and found nothing' — the agent must not conclude the web is empty
    when it was never actually queried."""


class WebSearchClient:
    def __init__(self, api_key: str | None = None, timeout: float = 30.0):
        s = get_settings()
        self.api_key = api_key if api_key is not None else s.tavily_api_key
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    def search(self, query: str, max_results: int = 5) -> list[WebResult]:
        if not self.api_key:
            raise WebSearchUnavailable(
                "Web search is not configured (TAVILY_API_KEY is unset)."
            )

        try:
            response = self._client.post(
                TAVILY_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                    # Deliberately off. include_answer returns Tavily's own
                    # LLM-generated summary — that would be a second model
                    # reasoning over the evidence before our agent sees it,
                    # exactly the LLM-over-LLM mistake we rejected when we
                    # declined to wrap Project 1's /chat.
                    "include_answer": False,
                    # Off for token cost: raw_content is whole pages.
                    "include_raw_content": False,
                },
            )
        except httpx.HTTPError as e:
            raise WebSearchUnavailable(f"Web search unreachable: {type(e).__name__}") from e

        if response.status_code == 200:
            return self._to_results(response.json())

        # 401/403 = bad key, 432/433 = Tavily's plan/credit exhaustion codes,
        # 429 = rate limited. All are "the tool is unusable right now", not
        # "the query was wrong", so the agent should replan around it rather
        # than rephrase and retry.
        if response.status_code in (401, 403):
            raise WebSearchUnavailable("Web search rejected our credential (check TAVILY_API_KEY).")
        if response.status_code in (429, 432, 433):
            raise WebSearchUnavailable("Web search quota exhausted.")
        raise WebSearchUnavailable(
            f"Web search returned {response.status_code}: {response.text[:200]}"
        )

    @staticmethod
    def _to_results(body: dict) -> list[WebResult]:
        results: list[WebResult] = []
        for item in body.get("results", []):
            try:
                results.append(
                    WebResult(
                        title=item.get("title") or "(untitled)",
                        url=item["url"],
                        content=(item.get("content") or "")[:MAX_CONTENT_CHARS],
                        score=item.get("score") or 0.0,
                    )
                )
            except (KeyError, TypeError, ValueError):
                # One malformed result degrades the set; it does not fail the
                # search. The agent still gets something to work with.
                continue
        return results


_client: WebSearchClient | None = None


def get_client() -> WebSearchClient:
    global _client
    if _client is None:
        _client = WebSearchClient()
    return _client


class WebSearchArgs(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=400,
        description="What to search the public web for.",
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=10,
        description="How many results to return. Default 5.",
    )


@registry.register(
    description=(
        "Search the public internet and return results with their title, URL, and "
        "an extract of the page content. Use this only for information that is not "
        "in the university's own documents: current events, anything published "
        "recently, external companies, job or internship openings elsewhere, or "
        "questions about the current state of the outside world. For university "
        "policies, rules, curriculum or campus information, use knowledge_search."
    ),
    timeout_s=35.0,
)
def web_search(args: WebSearchArgs) -> ToolResult:
    started = time.perf_counter()
    try:
        results = get_client().search(args.query, max_results=args.max_results)
    except WebSearchUnavailable as e:
        # unavailable, not failure — see knowledge.py for the same distinction.
        # "Web search is down" must not be reasoned about as "the web contains
        # nothing about this".
        return ToolResult.down(str(e))

    if not results:
        return ToolResult.success([], count=0, query=args.query)

    return ToolResult.success(
        [r.model_dump() for r in results],
        count=len(results),
        query=args.query,
        latency_ms=int((time.perf_counter() - started) * 1000),
        rendered="\n\n".join(r.render(i) for i, r in enumerate(results, start=1)),
    )
