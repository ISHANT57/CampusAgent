"""Per-request correlation ID.

Accepts an inbound `X-Request-ID` (so a frontend or gateway that already
minted one keeps it end to end), otherwise mints a UUID4. Bound into
structlog's contextvars so every log line emitted while handling this request
carries it with no logger plumbing, and echoed back on the response so a user
report ("it broke") can be matched to server-side logs.
"""

from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
