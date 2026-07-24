"""Identity resolution for the API, and the endpoint that hands a token out.

WHY THIS EXISTS: third-party cookies are dying.

The frontend (vercel.app) and API (onrender.com) are different sites, so the
identity cookie must be SameSite=None — a THIRD-PARTY cookie. Incognito, Brave,
Firefox strict mode, "block third-party cookies", and soon Chrome by default
all refuse it. When it is refused the cookie never sets, every request looks
like a new visitor, and the run-owned-by-identity check 404s the stream.
"Works in one profile, not another" is exactly that.

So identity is resolved from THREE sources, in order:

  1. X-Identity header     — normal fetch requests carry it
  2. ?token= query param   — EventSource cannot send headers, only a URL
  3. the cookie            — still works where third-party cookies are allowed

The token lives in the frontend's localStorage, which is always first-party and
always permitted. GET /identity issues one.

The token is signed but not a high-value secret: it grants read access to one
browser's OWN runs (its own goals), and clearing it only loses local history.
It does appear in the SSE URL, so it can land in access logs — an accepted
trade for EventSource, which offers no other way to authenticate.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.core.identity import COOKIE_NAME, MAX_AGE_SECONDS, Identity, resolve_or_issue

router = APIRouter(prefix="/identity", tags=["identity"])


def token_from_request(request: Request) -> str | None:
    return (
        request.headers.get("X-Identity")
        or request.query_params.get("token")
        or request.cookies.get(COOKIE_NAME)
    )


def current_identity(request: Request, response: Response) -> Identity:
    """Resolve the caller's identity from header, query, or cookie; mint one if
    none verifies. Set as a dependency, not middleware, so every endpoint that
    counts against an identity says so in its signature."""
    identity, is_new = resolve_or_issue(token_from_request(request))
    if is_new:
        # Still set the cookie for browsers that accept it — a bonus path, not
        # the one the app depends on. SameSite=None+Secure only over HTTPS;
        # see the long note in the previous cookie fix for why the scheme
        # decides (Render terminates TLS, uvicorn --proxy-headers restores it).
        cross_site = request.url.scheme == "https"
        response.set_cookie(
            COOKIE_NAME,
            identity.token,
            max_age=MAX_AGE_SECONDS,
            httponly=True,
            samesite="none" if cross_site else "lax",
            secure=cross_site,
        )
    return identity


class IdentityResponse(BaseModel):
    token: str


@router.get("", response_model=IdentityResponse)
def get_identity(request: Request, response: Response) -> IdentityResponse:
    """Return this browser's identity token.

    The frontend calls this once on load and stores the token in localStorage,
    then sends it explicitly on every request. That is what makes the app work
    where third-party cookies are blocked.

    Returns the SAME token if the browser already has a valid one (header,
    query, or cookie), a fresh one otherwise — so refreshing does not reset
    a returning visitor's history.
    """
    identity = current_identity(request, response)
    return IdentityResponse(token=identity.token)
