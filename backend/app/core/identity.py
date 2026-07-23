"""Anonymous browser identity for trial quota.

Trial mode requires no signup, so there is no user id to count runs against.
Something has to stand in, and the choice matters more than it looks.

WHY NOT IP:
Sitare students sit behind campus NAT. IP-keyed limits treat the entire
university as one user. Project 1 hit exactly this and wrote it down:

    "Anonymous callers have no user id, so rate_limit_key falls back to client
     IP - and a whole campus behind one NAT is a single IP. 20/minute would
     have meant 20 questions per minute for the entire college."
                              - CollegeRag backend/app/api/v1/chat.py

So: an HMAC-signed token issued on first contact and stored in a cookie is the
PRIMARY identity, and IP is only a coarse secondary ceiling.

WHAT THIS IS NOT:
Not authentication. Anyone can clear a cookie and get a new identity. Assume
roughly 10x the intended per-identity limit leaks through. That is acceptable
because the trial allowance is small (2 runs) and the control that actually
bounds cost is the global daily ceiling, not this.

What signing DOES buy: a client cannot mint identities faster than it can make
requests, cannot forge one that looks like someone else's, and cannot tamper
with the issue date. It converts trivial abuse into deliberate abuse.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from app.core.config import get_settings

COOKIE_NAME = "cb_identity"
# Long-lived: the point is to recognise a returning browser tomorrow, when its
# trial allowance has reset.
MAX_AGE_SECONDS = 90 * 24 * 3600


class InvalidIdentity(ValueError):
    """Token missing, malformed, expired, or with a bad signature."""


@dataclass(frozen=True)
class Identity:
    token: str          # the full signed value, returned to the browser
    subject: str        # the random part
    issued_at: int

    @property
    def key(self) -> str:
        """What quota is counted against.

        A hash, not the token itself: this lands in the database and in logs,
        and a value that can be replayed as a credential does not belong in
        either.
        """
        return hashlib.sha256(self.subject.encode()).hexdigest()[:32]


def _secret() -> bytes:
    return get_settings().app_secret.encode()


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def issue() -> Identity:
    subject = secrets.token_urlsafe(16)
    issued_at = int(time.time())
    payload = f"{subject}.{issued_at}"
    return Identity(token=f"{payload}.{_sign(payload)}", subject=subject, issued_at=issued_at)


def verify(token: str | None) -> Identity:
    if not token:
        raise InvalidIdentity("No identity token.")

    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidIdentity("Malformed identity token.")

    subject, issued_raw, signature = parts
    try:
        issued_at = int(issued_raw)
    except ValueError as e:
        raise InvalidIdentity("Malformed identity timestamp.") from e

    # Constant-time. A plain == leaks signature bytes through response latency,
    # the same reasoning as the service-key comparison in Project 1.
    if not hmac.compare_digest(_sign(f"{subject}.{issued_raw}"), signature):
        raise InvalidIdentity("Identity signature does not verify.")

    if time.time() - issued_at > MAX_AGE_SECONDS:
        raise InvalidIdentity("Identity token has expired.")

    return Identity(token=token, subject=subject, issued_at=issued_at)


def resolve_or_issue(token: str | None) -> tuple[Identity, bool]:
    """Return (identity, is_new).

    A tampered or expired token quietly yields a NEW identity rather than an
    error. This is not authentication — refusing service because a cookie was
    mangled would punish an ordinary user for a browser quirk, and the attacker
    it would inconvenience can just clear the cookie anyway.
    """
    try:
        return verify(token), False
    except InvalidIdentity:
        return issue(), True


def hash_ip(ip: str | None) -> str | None:
    """IPs are personal data and only equality is ever needed, so only the
    hash is stored. Salted with the app secret so the table cannot be reversed
    with a rainbow table over the (small) IPv4 space."""
    if not ip:
        return None
    return hashlib.sha256(_secret() + ip.encode()).hexdigest()[:32]
