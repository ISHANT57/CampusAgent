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
    """Follow redirects MANUALLY, validating every hop. Returns (final_url, text).

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
    timeout_s=30.0,
)
def web_read(args: WebReadArgs) -> ToolResult:
    try:
        final_url, text = fetch(args.url)
    except UnsafeURL as e:
        # A failure, not `unavailable`: the URL itself is the problem, so the
        # agent should try a different one rather than wait and retry.
        return ToolResult.failure(str(e))
    except httpx.HTTPStatusError as e:
        return ToolResult.failure(f"{args.url} returned HTTP {e.response.status_code}.")
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
