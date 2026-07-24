"""Provider catalogue and connection testing.

    GET  /providers        what the picker renders
    POST /providers/test   verify a key before a real run depends on it

The catalogue is served straight from catalogue.json, which is why adding a
vendor stays a data change: the frontend picker gains an option without a
frontend deploy.
"""

# No `from __future__ import annotations` — slowapi wraps these handlers and
# FastAPI would then try to resolve their types against slowapi's namespace.

import time

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core.identity import Identity
from app.core.rate_limit import RUN_CREATE_LIMIT, RUN_READ_LIMIT, limiter
from app.llm.base import LLMError, Message, ToolSpec
from app.llm.manager import NoProviderAvailable, build_provider, catalogue, supports_tools
from app.api.v1.identity_dep import current_identity

router = APIRouter(prefix="/providers", tags=["providers"])


class ModelInfo(BaseModel):
    id: str
    label: str
    supports_tools: bool
    notes: str | None = None


class ProviderInfo(BaseModel):
    id: str
    label: str
    blurb: str
    requires_key: bool
    allows_custom_base_url: bool
    keys_url: str | None = None
    default_model: str | None = None
    models: list[ModelInfo]


@router.get("", response_model=list[ProviderInfo])
@limiter.limit(RUN_READ_LIMIT)
def list_providers(request: Request):
    """The picker's data.

    Returns NO credentials — not the hosted key, not a user's. It is
    deliberately public: it is a menu, and knowing that Groq exists is not a
    secret.
    """
    out = []
    for pid, entry in catalogue().items():
        out.append(ProviderInfo(
            id=pid,
            label=entry["label"],
            blurb=entry["blurb"],
            requires_key=entry.get("requires_key", True),
            allows_custom_base_url=entry.get("allows_custom_base_url", False),
            keys_url=entry.get("keys_url"),
            default_model=entry.get("default_model"),
            models=[
                ModelInfo(
                    id=m["id"], label=m["label"],
                    supports_tools=bool(m.get("supports_tools")), notes=m.get("notes"),
                )
                # A model that cannot call tools cannot run this agent. Offering
                # one would produce an assistant that appears to refuse every
                # task, with no error anywhere (M0/E2).
                for m in entry.get("models", []) if m.get("supports_tools")
            ],
        ))
    return out


class TestRequest(BaseModel):
    provider: str
    api_key: str = ""
    model: str | None = None
    base_url: str | None = None


class TestResult(BaseModel):
    ok: bool
    provider: str
    model: str | None = None
    latency_ms: int | None = None
    tool_calling: bool | None = None
    error: str | None = None
    reason: str | None = None


# The smallest possible tool. The test is not "can it reason" but "does a tool
# call come back at all".
_PROBE_TOOL = ToolSpec(
    name="report_ready",
    description="Call this to confirm you can call tools. Pass status='ok'.",
    parameters={
        "type": "object",
        "properties": {"status": {"type": "string", "description": "Always 'ok'."}},
        "required": ["status"],
    },
)


@router.post("/test", response_model=TestResult)
# Rate-limited like a run, not like a read: each test is a real provider call.
@limiter.limit(RUN_CREATE_LIMIT)
def test_provider(
    request: Request,
    payload: TestRequest,
    identity: Identity = Depends(current_identity),
):
    """One real call, reporting three things.

    Connectivity, model, and — the one that matters — whether TOOL CALLING
    actually works. M0/E2 found models that accept a `tools` parameter and
    silently ignore it. Without this check that surfaces at step 4 of a real
    run as an agent that mysteriously refuses to act; with it, it surfaces
    during setup with a clear message.

    The key is used and discarded. It is never persisted, and any error text is
    redacted before it reaches a response.
    """
    from app.core.redaction import redact_text

    declared = supports_tools(payload.provider, payload.model) if payload.model else None

    try:
        provider = build_provider(
            payload.provider, api_key=payload.api_key,
            model=payload.model, base_url=payload.base_url,
        )
    except NoProviderAvailable as e:
        return TestResult(ok=False, provider=payload.provider,
                          error=redact_text(str(e)), reason=e.reason)

    started = time.perf_counter()
    try:
        completion = provider.complete(
            [Message(role="user", content="Call report_ready with status 'ok'.")],
            tools=[_PROBE_TOOL],
        )
    except LLMError as e:
        return TestResult(
            ok=False, provider=payload.provider, model=provider.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=redact_text(str(e)), reason=type(e).__name__,
        )
    except Exception as e:  # noqa: BLE001
        return TestResult(ok=False, provider=payload.provider, model=provider.model,
                          error=redact_text(f"{type(e).__name__}: {e}"), reason="unexpected")

    tool_calling = bool(completion.tool_calls)
    return TestResult(
        ok=True,
        provider=payload.provider,
        model=completion.model or provider.model,
        latency_ms=completion.latency_ms,
        tool_calling=tool_calling,
        # ok=True means the credential works. tool_calling=False still needs
        # saying loudly, because the agent will not function.
        error=None if tool_calling else (
            "Connected, but this model did not return a tool call. The agent "
            "needs tool calling and will not work with it."
            + ("" if declared is not False else " The catalogue also lists it as unsupported.")
        ),
    )
