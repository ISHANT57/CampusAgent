"""Anthropic provider (Messages API).

A third wire format, not a fourth vendor on an existing one — catalogue.json's
own rule ("configuration, not code" covers new vendors, not new protocols).
Three things Anthropic does differently from both existing adapters:

  system prompt   a top-level `system` field, like Gemini's systemInstruction
                  (not a message, unlike the OpenAI-compatible format)
  tool arguments  a real JSON object on `input`, like Gemini (not a string to
                  parse, unlike the OpenAI-compatible format)
  tool schema     JSON Schema taken as-is under `input_schema`, like the
                  OpenAI-compatible format (not translated, unlike Gemini)

So it sits between the other two adapters rather than duplicating either.

This file is the only place in the codebase that knows Anthropic's wire format.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.llm.base import (
    Completion,
    LLMPermanentError,
    LLMTransientError,
    Message,
    ToolCall,
    ToolSpec,
    Usage,
)

BASE_URL = "https://api.anthropic.com/v1/messages"

# Pinned, not "latest" — an undocumented version bump changing the wire format
# under a fixed adapter is exactly the kind of drift M0 was built to catch by
# measurement rather than assumption.
API_VERSION = "2023-06-01"

# Anthropic's error `type` is a documented, stable enum — matching the
# codebase's established code-first approach (see openai_compatible.py's
# _TRANSIENT_CODES): a Groq 429 whose prose happened to contain "billing" was
# once misclassified permanent by substring matching, which is why every
# adapter after that incident checks the machine-readable field first.
_TRANSIENT_TYPES = frozenset({"rate_limit_error", "api_error", "overloaded_error"})
_PERMANENT_TYPES = frozenset({
    "invalid_request_error", "authentication_error", "permission_error",
    "not_found_error", "request_too_large",
})


class AnthropicProvider:
    """Conforms to LLMProvider structurally. Inherits from nothing — Protocol
    conformance needs no base class."""

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str = "",
        model: str = "",
        timeout: float = 90.0,
        max_output_tokens: int = 1024,
    ):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        # One client, reused. Reconnecting per call would add a TLS handshake
        # to each of the 10-15 calls in a run.
        self._client = httpx.Client(timeout=timeout)

    # -- request building -----------------------------------------------------

    def _build_body(
        self, messages: list[Message], tools: list[ToolSpec] | None, temperature: float
    ) -> dict[str, Any]:
        # Anthropic takes the system prompt as a separate top-level field, not
        # a message — same shape as Gemini's systemInstruction. Several system
        # messages are concatenated rather than dropped, so silently losing one
        # does not change behaviour invisibly.
        system_text = "\n\n".join(m.content for m in messages if m.role == "system")

        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,  # required by this API, unlike OpenAI's
            "messages": [
                {"role": m.role, "content": m.content} for m in messages if m.role != "system"
            ],
            "temperature": temperature,
        }
        if system_text:
            body["system"] = system_text
        if tools:
            body["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    # JSON Schema taken as-is, like the OpenAI-compatible
                    # format — Gemini is the one that needs dialect translation.
                    "input_schema": t.parameters,
                }
                for t in tools
            ]
        return body

    # -- error mapping ----------------------------------------------------------

    def _raise_for_error(self, response: httpx.Response) -> None:
        error_type = ""
        try:
            error = response.json().get("error") or {}
            detail = str(error.get("message", ""))
            error_type = str(error.get("type", ""))
        except Exception:
            detail = response.text[:300]

        kwargs = {"provider": self.name, "model": self.model, "status": response.status_code}
        label = f"Anthropic {response.status_code}"

        if error_type in _TRANSIENT_TYPES:
            raise LLMTransientError(f"{label}: {detail[:220]}", **kwargs)
        if error_type in _PERMANENT_TYPES:
            raise LLMPermanentError(f"{label}: {detail[:220]}", **kwargs)

        # No recognised type — fall back to the status code. 529 is
        # Anthropic-specific ("overloaded"), included alongside 429/5xx.
        if response.status_code in (429, 529) or response.status_code >= 500:
            raise LLMTransientError(f"{label}: {detail[:220]}", **kwargs)
        raise LLMPermanentError(f"{label}: {detail[:220]}", **kwargs)

    # -- response parsing ---------------------------------------------------

    @staticmethod
    def _parse(raw: dict[str, Any]) -> tuple[str | None, list[ToolCall], str | None]:
        """Anthropic mixes text and tool_use blocks in one `content` list, so
        both are collected in a single pass — the same shape as Gemini's
        `parts`, for the same reason: either can appear, in any order."""
        blocks = raw.get("content") or []

        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in blocks:
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                # `input` is already an object — no json.loads, so the
                # MALFORMED_JSON failure class is structurally impossible on
                # this path, same as Gemini's `args`.
                args = block.get("input")
                calls.append(ToolCall(
                    name=block.get("name", ""),
                    arguments=args if isinstance(args, dict) else {},
                    id=block.get("id"),
                ))

        return ("\n".join(texts) if texts else None), calls, raw.get("stop_reason")

    # -- the contract ---------------------------------------------------------

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        body = self._build_body(messages, tools, temperature)
        started = time.perf_counter()

        try:
            response = self._client.post(
                BASE_URL,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": API_VERSION,
                    "Content-Type": "application/json",
                },
                json=body,
            )
        except httpx.TimeoutException as e:
            raise LLMTransientError(
                f"Anthropic timeout: {e}", provider=self.name, model=self.model
            ) from e
        except httpx.HTTPError as e:
            raise LLMTransientError(
                f"Anthropic network error: {e}", provider=self.name, model=self.model
            ) from e

        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code != 200:
            self._raise_for_error(response)

        raw = response.json()
        text, calls, finish = self._parse(raw)
        usage = raw.get("usage") or {}

        return Completion(
            text=text,
            tool_calls=calls,
            usage=Usage(
                prompt_tokens=usage.get("input_tokens") or 0,
                completion_tokens=usage.get("output_tokens") or 0,
            ),
            model=raw.get("model") or self.model,
            finish_reason=finish,
            latency_ms=latency_ms,
            raw=raw,
        )
