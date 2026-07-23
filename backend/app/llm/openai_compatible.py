"""One adapter, six providers.

OpenRouter, Groq, GitHub Models, OpenAI, Ollama and any custom endpoint all
speak the OpenAI Chat Completions format. They differ only in base URL, auth
header, and model id — which is why those live in catalogue.json and this file
is written once.

The one thing this format gets WRONG compared to Gemini: tool arguments arrive
as a JSON **string** that must be parsed. That makes a whole failure class
(malformed JSON) possible here which is structurally impossible on Gemini's
path, where `args` is already an object. M0 measured this; it is the single
biggest reliability difference between the two shapes.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.llm.base import (
    Completion,
    LLMParseError,
    LLMPermanentError,
    LLMTransientError,
    Message,
    ToolCall,
    ToolSpec,
    Usage,
)

# Substrings marking a failure as structural rather than temporary. M0/F2: two
# 429s can mean opposite things, and only the body distinguishes them.
# "free-models-per-day" is OpenRouter's daily cap — retrying before midnight UTC
# cannot succeed, so it must not be treated as transient.
_PERMANENT_MARKERS = (
    "free-models-per-day",
    "invalid api key",
    "incorrect api key",
    "no such model",
    "does not exist",
    "insufficient_quota",
    "billing",
)


class OpenAICompatibleProvider:
    """Conforms to LLMProvider structurally. Inherits from nothing — Protocol
    conformance needs no base class."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key: str = "",
        auth: str = "bearer",
        timeout: float = 90.0,
        max_output_tokens: int = 1024,
        extra_headers: dict[str, str] | None = None,
    ):
        self.name = name
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.auth = auth
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self.extra_headers = extra_headers or {}
        # One client per provider instance, reused across the ~3 calls of a run.
        self._client = httpx.Client(timeout=timeout)

    # -- request ------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.auth == "bearer" and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif self.auth.startswith("header:") and self.api_key:
            headers[self.auth.split(":", 1)[1]] = self.api_key
        # auth == "none" (Ollama) sends nothing.
        return headers

    def _build_body(
        self, messages: list[Message], tools: list[ToolSpec] | None, temperature: float
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            # This format takes the system prompt as a normal message, unlike
            # Gemini's separate systemInstruction field.
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": self.max_output_tokens,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        # No dialect translation: this format takes JSON Schema
                        # as-is. Gemini is the one that needs conversion.
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        return body

    # -- errors -------------------------------------------------------------

    def _raise_for_error(self, response: httpx.Response) -> None:
        try:
            payload = response.json()
            error = payload.get("error") or {}
            detail = str(error.get("message") or payload.get("detail") or response.text[:300])
            # OpenRouter nests the upstream provider's real message here, and
            # it is where the daily-cap wording actually appears.
            metadata = error.get("metadata") or {}
            if metadata.get("raw"):
                detail = f"{detail} | {metadata['raw']}"
        except Exception:
            detail = response.text[:300]

        kwargs = {"provider": self.name, "model": self.model, "status": response.status_code}
        lowered = detail.lower()

        if any(marker in lowered for marker in _PERMANENT_MARKERS):
            raise LLMPermanentError(f"{self.name} {response.status_code}: {detail[:220]}", **kwargs)
        if response.status_code == 429 or response.status_code >= 500:
            raise LLMTransientError(f"{self.name} {response.status_code}: {detail[:220]}", **kwargs)
        raise LLMPermanentError(f"{self.name} {response.status_code}: {detail[:220]}", **kwargs)

    # -- response -----------------------------------------------------------

    def _parse(self, raw: dict[str, Any]) -> tuple[str | None, list[ToolCall], str | None]:
        choice = (raw.get("choices") or [{}])[0]
        message = choice.get("message") or {}

        calls: list[ToolCall] = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            args_raw = fn.get("arguments")

            if isinstance(args_raw, dict):
                # Off-spec but common. Accept it rather than fail — some
                # OpenAI-compatible servers return an object.
                arguments = args_raw
            elif isinstance(args_raw, str):
                try:
                    arguments = json.loads(args_raw) if args_raw.strip() else {}
                except json.JSONDecodeError as e:
                    # The failure class that cannot occur on Gemini. Raised as
                    # a distinct type because the fix is a repair prompt, not
                    # a retry — the provider is fine, the output was not.
                    raise LLMParseError(
                        f"{self.name} returned unparseable tool arguments: {e}",
                        provider=self.name, model=self.model,
                    ) from e
            else:
                arguments = {}

            calls.append(ToolCall(name=fn.get("name", ""), arguments=arguments, id=call.get("id")))

        return message.get("content"), calls, choice.get("finish_reason")

    # -- contract -----------------------------------------------------------

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
                f"{self.base_url}/chat/completions", headers=self._headers(), json=body
            )
        except httpx.TimeoutException as e:
            raise LLMTransientError(
                f"{self.name} timed out after {self.timeout}s", provider=self.name, model=self.model
            ) from e
        except httpx.HTTPError as e:
            raise LLMTransientError(
                f"{self.name} network error: {type(e).__name__}",
                provider=self.name, model=self.model,
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
                prompt_tokens=usage.get("prompt_tokens") or 0,
                completion_tokens=usage.get("completion_tokens") or 0,
            ),
            model=raw.get("model") or self.model,
            finish_reason=finish,
            latency_ms=latency_ms,
            raw=raw,
        )
