"""Gemini provider (generateContent).

Frozen as primary by M0: `gemini-2.5-flash` scored 100% format compliance and
100% tool-selection accuracy, at the lowest token cost of the five models
tested (324 avg tokens/call vs 945 for the OpenRouter baseline).

This file is the only place in the codebase that knows Gemini's wire format.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.config import get_settings
from app.llm.base import (
    Completion,
    LLMPermanentError,
    LLMTransientError,
    Message,
    ToolCall,
    ToolSpec,
    Usage,
)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Keys Pydantic emits that Gemini's schema dialect rejects. Gemini takes an
# OpenAPI 3.0 subset, NOT JSON Schema.
#
# $ref/$defs are the dangerous ones: Pydantic emits them for any nested model,
# so a tool with a nested args model produces a schema Gemini 400s on. M0/E6
# confirmed that once these are stripped, enums, arrays, AND nested objects all
# survive — so tool args do not need to be flattened, they need translating.
_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {"$schema", "$defs", "$ref", "additionalProperties", "title", "examples", "default"}
)

# Substrings that mark a 429 as structural rather than temporary. M0/F2:
# gemini-2.0-flash returns `limit: 0` because its free tier was retired — that
# is not throttling, and retrying it can never succeed.
_PERMANENT_MARKERS = ("limit: 0", "no longer available", "is not supported", "API key not valid")


def translate_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema -> Gemini's OpenAPI 3.0 subset. Recursive, drops what it
    cannot express rather than failing, because a dropped `title` is harmless
    and a rejected request is not."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


class GeminiProvider:
    """Conforms to LLMProvider structurally. Deliberately does NOT inherit
    from it — Protocol conformance needs no base class."""

    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float | None = None):
        s = get_settings()
        self.api_key = api_key or s.gemini_api_key
        self.model = model or s.gemini_model
        self.timeout = timeout or s.llm_timeout
        self.max_output_tokens = s.llm_max_output_tokens
        # One client, reused. Reconnecting per call would add a TLS handshake
        # to each of the 10-15 calls in a run.
        self._client = httpx.Client(timeout=self.timeout)

    # -- request building ---------------------------------------------------

    def _build_body(
        self, messages: list[Message], tools: list[ToolSpec] | None, temperature: float
    ) -> dict[str, Any]:
        # Gemini takes the system prompt as a separate top-level field, not as
        # a message. Several system messages are concatenated rather than
        # dropped — silently losing one would change behaviour invisibly.
        system_text = "\n\n".join(m.content for m in messages if m.role == "system")

        contents = [
            {
                # Gemini's assistant role is "model". Anything not from the
                # assistant is "user" — including tool results, which M0/E5
                # proved works.
                "role": "model" if m.role == "assistant" else "user",
                "parts": [{"text": m.content}],
            }
            for m in messages
            if m.role != "system"
        ]

        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": self.max_output_tokens,
            },
        }
        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": translate_schema(t.parameters),
                        }
                        for t in tools
                    ]
                }
            ]
        return body

    # -- error mapping ------------------------------------------------------

    def _raise_for_error(self, response: httpx.Response) -> None:
        """Map an HTTP failure onto the transient/permanent split.

        The body is parsed, not just the status. M0/F2: two 429s that look
        identical at the status line demand opposite handling, and getting it
        wrong either burns a run's budget retrying the impossible or gives up
        on a blip.
        """
        try:
            detail = str((response.json().get("error") or {}).get("message", ""))
        except Exception:
            detail = response.text[:300]

        kwargs = {"provider": self.name, "model": self.model, "status": response.status_code}

        if any(marker in detail for marker in _PERMANENT_MARKERS):
            raise LLMPermanentError(f"Gemini {response.status_code}: {detail[:200]}", **kwargs)
        if response.status_code == 429 or response.status_code >= 500:
            raise LLMTransientError(f"Gemini {response.status_code}: {detail[:200]}", **kwargs)
        # 400/401/403/404 — a request or credential problem. Retrying an
        # identical request cannot fix either.
        raise LLMPermanentError(f"Gemini {response.status_code}: {detail[:200]}", **kwargs)

    # -- response parsing ---------------------------------------------------

    @staticmethod
    def _parse(raw: dict[str, Any]) -> tuple[str | None, list[ToolCall], str | None]:
        """Gemini mixes text and function calls in one `parts` list, so both
        are collected in a single pass rather than assuming which came back."""
        candidate = (raw.get("candidates") or [{}])[0]
        parts = ((candidate.get("content") or {}).get("parts")) or []

        texts: list[str] = []
        calls: list[ToolCall] = []
        for part in parts:
            if "text" in part:
                texts.append(part["text"])
            if "functionCall" in part:
                fc = part["functionCall"]
                # `args` is already an object — no json.loads, so the whole
                # MALFORMED_JSON failure class is structurally impossible on
                # this path. OpenRouter's string form is not so lucky.
                args = fc.get("args")
                calls.append(
                    ToolCall(name=fc.get("name", ""), arguments=args if isinstance(args, dict) else {})
                )

        return ("\n".join(texts) if texts else None), calls, candidate.get("finishReason")

    # -- the contract -------------------------------------------------------

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
                BASE_URL.format(model=self.model),
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=body,
            )
        except httpx.TimeoutException as e:
            # Transient: the request may simply have been slow. Free-tier
            # latency is spiky (M0 measured a 63s outlier on another provider).
            raise LLMTransientError(f"Gemini timeout: {e}", provider=self.name, model=self.model) from e
        except httpx.HTTPError as e:
            raise LLMTransientError(f"Gemini network error: {e}", provider=self.name, model=self.model) from e

        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code != 200:
            self._raise_for_error(response)

        raw = response.json()
        text, calls, finish = self._parse(raw)
        usage = raw.get("usageMetadata") or {}

        return Completion(
            text=text,
            tool_calls=calls,
            usage=Usage(
                prompt_tokens=usage.get("promptTokenCount") or 0,
                completion_tokens=usage.get("candidatesTokenCount") or 0,
            ),
            model=self.model,
            finish_reason=finish,
            latency_ms=latency_ms,
            raw=raw,
        )
