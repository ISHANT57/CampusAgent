"""Security response headers.

This is a JSON API, not a page — the browser never renders API responses as
HTML, so a locked-down CSP costs nothing here. `/docs` and `/redoc` are the one
exception: FastAPI's built-in Swagger/ReDoc UI loads its JS/CSS from
cdn.jsdelivr.net, so a blanket `default-src 'none'` would break the docs page
itself rather than protect anything.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_DOC_PATHS = {"/docs", "/redoc", "/openapi.json"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path not in _DOC_PATHS:
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response
