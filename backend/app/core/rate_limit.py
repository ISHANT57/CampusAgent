"""API rate limiting.

POST /runs is unauthenticated and starts LLM work. Without a limit it is
trivially scriptable — and even in BYOK mode, where the caller pays for the
inference, a script can still fill the database, exhaust the connection pool,
and monopolise the single Render instance.

KEYED BY IDENTITY, NOT IP.
Sitare students sit behind campus NAT. An IP-keyed limit treats the whole
university as one caller, which is the exact failure Project 1 documented:

    "a whole campus behind one NAT is a single IP. 20/minute would have meant
     20 questions per minute for the entire college."
                              - CollegeRag backend/app/api/v1/chat.py

The signed identity cookie gives a per-browser key instead. It is not
authentication — a cookie can be cleared — but it converts "one student breaks
it for everyone" into "one browser has to work at it", and IP remains the
fallback for a client that sends no cookie at all.
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.identity import COOKIE_NAME, InvalidIdentity, verify


def rate_limit_key(request: Request) -> str:
    """Per-browser when the identity cookie verifies, per-IP otherwise.

    A tampered cookie deliberately falls through to IP rather than being
    rejected: an attacker who forges garbage should not be able to opt OUT of
    rate limiting by sending an invalid token.
    """
    try:
        identity = verify(request.cookies.get(COOKIE_NAME))
        return f"id:{identity.key}"
    except InvalidIdentity:
        return f"ip:{get_remote_address(request)}"


# No storage_uri: slowapi defaults to in-memory counters, which is correct for
# a single Render instance. With more than one process these become per-process
# and under-limit in aggregate — that is the point at which a shared store
# (Upstash Redis free tier) earns its keep.
#
# ponytail: in-memory counters, single instance. Swap for a shared store at
# instance #2.
limiter = Limiter(key_func=rate_limit_key)

# Starting a run is expensive: it holds a background task, a database session,
# and several provider calls. This is deliberately loose enough not to annoy a
# real user exploring the tool, and tight enough to stop a script.
RUN_CREATE_LIMIT = "10/minute"
# Reads are cheap and a browser polls them while watching a run.
RUN_READ_LIMIT = "120/minute"
