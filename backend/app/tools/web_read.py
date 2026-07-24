"""Fetch one URL and return its readable text.

The highest-risk tool in the project, for two independent reasons:

  SSRF        the URL comes from a model, which got it from a user or from
              another web page. "Fetch this URL" from inside a datacentre
              reaches the cloud metadata endpoint, the local network, and
              every internal service that trusts its own subnet.
  INJECTION   the returned text is fully attacker-controlled. Anyone can
              publish a page and get the agent to read it.

Both are mitigated, neither is solved:

  - Every hop is resolved to an IP and checked against private, loopback,
    link-local, multicast and reserved ranges. Redirects are followed manually
    so each hop is re-checked; a public URL that 302s to 169.254.169.254 is
    the classic bypass.
  - Only http/https. Not file://, gopher://, ftp://, data:.
  - Content is truncated, so an injected wall of text cannot dominate the
    prompt.
  - Response size is capped during streaming, not after, so a multi-gigabyte
    body cannot exhaust memory before the check runs.

The real containment is that every tool in this agent is READ-ONLY. Injection
can produce a wrong answer; it cannot produce a wrong action. That property is
what must hold before any effectful tool ships (M38, gated on M43).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field
from selectolax.parser import HTMLParser

from app.core.config import get_settings
from app.tools.base import ToolResult
from app.tools.registry import registry

MAX_CONTENT_CHARS = 8_000
MAX_BYTES = 4_000_000
MAX_REDIRECTS = 3
ALLOWED_SCHEMES = frozenset({"http", "https"})

# Stripped before text extraction: these carry no prose and are where injected
# instructions most often hide from a human reader.
_NOISE = "script, style, noscript, nav, header, footer, aside, form, svg, iframe"


class UnsafeURL(ValueError):
    """The URL points somewhere a server-side fetcher must not go."""


def _check_host(hostname: str) -> str:
    """Resolve a hostname and reject any non-public address.

    Resolution matters: a domain an attacker controls can simply have an A
    record pointing at 127.0.0.1 or 169.254.169.254, so validating the string
    alone proves nothing.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise UnsafeURL(f"Could not resolve {hostname}") from e

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        # is_global is False for private, loopback, link-local, multicast,
        # reserved and unspecified ranges. One check instead of an
        # enumeration that will miss a range — an allow-list, not a deny-list.
        if not ip.is_global:
            raise UnsafeURL(
                f"{hostname} resolves to the non-public address {ip}, which this "
                "tool will not fetch."
            )
    return hostname


def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeURL(f"Only http and https are allowed, got {parsed.scheme or '(none)'!r}.")
    if not parsed.hostname:
        raise UnsafeURL("URL has no hostname.")
    _check_host(parsed.hostname)
    return url


def extract_text(html: str) -> str:
    tree = HTMLParser(html)
    for node in tree.css(_NOISE):
        node.decompose()
    body = tree.body or tree.root
    text = body.text(separator="\n", strip=True) if body else ""
    # Collapse the blank-line runs that stripping tags leaves behind.
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def fetch(url: str, timeout: float = 20.0) -> tuple[str, str]:
    """Read one URL. Returns (final_url, text).

    Two backends, chosen by config and invisible to the agent — the same
    pattern as the LLM providers:

      Firecrawl (if FIRECRAWL_API_KEY is set)
          Renders JavaScript and returns clean markdown. The built-in fetcher
          below reads raw HTML, which is an empty shell on a client-rendered
          site — and sitare.org is exactly that (a React/Vite app), so the one
          URL a Sitare student is most likely to paste is the one the built-in
          path fails on.

      Built-in (httpx + selectolax)
          No dependency, no third-party quota. Fine for server-rendered pages.

    The entry URL is validated for BOTH paths. That is defence in depth for
    Firecrawl — the request originates from THEIR servers, so the SSRF surface
    is theirs, but validating first means we never even ask them to fetch a
    private address, so the agent cannot be turned into a proxy to the internal
    network via Firecrawl either.
    """
    current = validate_url(url)
    if get_settings().firecrawl_api_key:
        return _fetch_via_firecrawl(current, timeout)
    return _fetch_builtin(current, timeout)


def _fetch_via_firecrawl(url: str, timeout: float) -> tuple[str, str]:
    """Scrape via Firecrawl, returning rendered markdown.

    Firecrawl follows redirects on its own infrastructure, so the manual
    per-hop revalidation the built-in path needs does not apply here — a
    redirect into a private address hits Firecrawl's network, not ours.
    """
    key = get_settings().firecrawl_api_key
    response = httpx.post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "url": url,
            "formats": ["markdown"],
            # Strip nav/footer/ads server-side — the same noise extract_text
            # removes for the built-in path, and where injected instructions
            # most often hide.
            "onlyMainContent": True,
        },
        # Firecrawl renders JS, so it is slower than a raw GET. Its own scrape
        # can take a while; give it room but stay under the tool timeout.
        timeout=max(timeout, 45.0),
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("success", True):
        raise UnsafeURL(f"Firecrawl could not read the page: {str(body.get('error'))[:160]}")

    data = body.get("data") or {}
    markdown = data.get("markdown") or ""
    final = (data.get("metadata") or {}).get("sourceURL") or url
    return final, markdown[:MAX_CONTENT_CHARS]


def _fetch_builtin(url: str, timeout: float = 20.0) -> tuple[str, str]:
    """Follow redirects MANUALLY, validating every hop.

    httpx's follow_redirects=True would validate only the first URL — and the
    whole point of an SSRF redirect attack is that hop 1 is a perfectly public
    address.
    """
    current = validate_url(url)

    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            with client.stream("GET", current, headers={"User-Agent": "CampusBrainAgent/0.1"}) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise UnsafeURL("Redirect without a Location header.")
                    current = validate_url(str(response.url.join(location)))
                    continue

                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if not any(t in content_type for t in ("text/html", "text/plain", "application/xhtml")):
                    raise UnsafeURL(f"Unsupported content type: {content_type or 'unknown'}")

                # Capped DURING streaming. Checking Content-Length afterwards
                # trusts a header the server controls, and reading first then
                # checking defeats the purpose.
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        break
                    chunks.append(chunk)
                raw = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")

            text = extract_text(raw) if "html" in content_type else raw
            return current, text[:MAX_CONTENT_CHARS]

    raise UnsafeURL(f"Too many redirects (more than {MAX_REDIRECTS}).")


class WebReadArgs(BaseModel):
    url: str = Field(
        max_length=2000,
        description="The full URL to fetch, including https://.",
    )


@registry.register(
    description=(
        "Fetch one specific web page and return its readable text. Use this when "
        "the user gives you a URL directly, or when a web_search result needs to be "
        "read in full. Requires a complete URL including https://. Returns the "
        "page's text content only."
    ),
    # Generous: Firecrawl renders JavaScript before returning, which is slower
    # than a raw GET. Harmless for the built-in path, which returns in under a
    # second and never approaches this.
    timeout_s=55.0,
)
def web_read(args: WebReadArgs) -> ToolResult:
    try:
        final_url, text = fetch(args.url)
    except UnsafeURL as e:
        # A failure, not `unavailable`: the URL itself is the problem, so the
        # agent should try a different one rather than wait and retry.
        return ToolResult.failure(str(e))
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        # A Firecrawl quota/outage (402/429/5xx) is the TOOL being unavailable,
        # not the URL being bad — so the agent should say so rather than
        # conclude the page is unreadable. A genuine 4xx from the target page
        # is a real read failure.
        if status in (402, 408, 429) or status >= 500:
            return ToolResult.down(f"The page reader is temporarily unavailable (HTTP {status}).")
        return ToolResult.failure(f"{args.url} returned HTTP {status}.")
    except httpx.HTTPError as e:
        return ToolResult.down(f"Could not reach {args.url}: {type(e).__name__}")

    if not text.strip():
        return ToolResult.success("", count=0, url=final_url, rendered="(the page had no readable text)")

    return ToolResult.success(
        text,
        count=len(text),
        url=final_url,
        rendered=f"{final_url}\n\n{text}",
    )
