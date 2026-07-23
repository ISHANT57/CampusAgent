"""SSRF guard for user-supplied provider base URLs.

THE SHARPEST RISK THIS FEATURE INTRODUCES.

"Bring your own OpenAI-compatible endpoint" means the user hands us a URL and
OUR SERVER makes a POST to it. Point it at http://169.254.169.254/… and the app
becomes an SSRF proxy into its own infrastructure — cloud metadata, internal
services, anything that trusts its own subnet.

The same logic already guards `web_read`. It lives in its own module rather
than being imported from a tool, because a security control shared between the
tool layer and the provider layer should not depend on either.

WHY HOSTNAMES ARE RESOLVED, NOT PATTERN-MATCHED:
validating the string proves nothing. An attacker who controls a domain simply
points its A record at 127.0.0.1 or 169.254.169.254. Only the resolved address
tells the truth.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = frozenset({"http", "https"})


class UnsafeProviderURL(ValueError):
    """The base URL points somewhere a server-side client must not go."""


def validate_provider_url(url: str, *, allow_loopback: bool = False) -> str:
    """Reject any base URL that resolves outside the public internet.

    `allow_loopback` exists for exactly one reason: Ollama is MEANT to be
    http://localhost:11434. That is a named, deliberate exception rather than a
    hole in the check — and it is only ever safe when the app runs on the same
    machine as the user. In a hosted deployment there is no user machine, so
    BYOK_ALLOW_LOOPBACK must stay false in production.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeProviderURL(
            f"Provider URL must use http or https, got {parsed.scheme or '(none)'!r}."
        )
    if not parsed.hostname:
        raise UnsafeProviderURL("Provider URL has no hostname.")

    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as e:
        raise UnsafeProviderURL(f"Could not resolve {parsed.hostname}.") from e

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])

        if allow_loopback and ip.is_loopback:
            continue

        # is_global is False for private, loopback, link-local, multicast,
        # reserved and unspecified ranges. One check instead of an enumeration
        # that will eventually miss a range — an allow-list, not a deny-list.
        if not ip.is_global:
            raise UnsafeProviderURL(
                f"{parsed.hostname} resolves to {ip}, which is not a public address. "
                "Provider endpoints must be reachable on the public internet."
            )

    return url
