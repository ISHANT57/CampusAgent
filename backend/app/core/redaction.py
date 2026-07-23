"""Strip credentials before anything is persisted.

WHY ON WRITE, NOT ON DISPLAY:
the display path is not the one that gets exported, dumped to a log
aggregator, or handed to a support engineer. Redacting at render time leaves
the secret sitting in Postgres, which is exactly where a leak comes from.

WHAT REACHES HERE:
`steps` stores tool inputs, outputs and errors. Provider errors echo request
context freely, and a BYOK user's key travels through the same code paths as
everything else. One badly-worded upstream error is all it takes.

This is defence in depth. The primary control is that keys are never put into
step payloads deliberately — this catches the case where a library puts one
there on our behalf.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

# Shape-based patterns, so an unknown provider's key is caught too. Ordered
# longest-prefix-first: sk-or-v1- must match before a bare sk-.
_PATTERNS = [
    re.compile(r"sk-or-v1-[A-Za-z0-9_-]{16,}"),      # OpenRouter
    re.compile(r"sk-proj-[A-Za-z0-9_-]{16,}"),        # OpenAI project keys
    re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"),         # Anthropic
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),              # Groq
    re.compile(r"AIza[A-Za-z0-9_-]{30,}"),            # Google
    re.compile(r"tvly-[A-Za-z0-9_-]{16,}"),           # Tavily
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),              # GitHub
    re.compile(r"npg_[A-Za-z0-9]{16,}"),              # Neon
    re.compile(r"sk-[A-Za-z0-9]{20,}"),               # generic OpenAI-style
    # Credentials embedded in a URL — a connection string in an error message.
    re.compile(r"(?<=://)[^:/@\s]+:[^@/\s]+(?=@)"),
    # An Authorization header echoed back verbatim.
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"),
]

# Keys whose VALUE we know, so they are caught even if the shape is unusual
# (a self-hosted endpoint's token, say). Read lazily: settings may not be
# importable at module load in every context.
_known_cache: list[str] | None = None


def _known_secrets() -> list[str]:
    global _known_cache
    if _known_cache is None:
        from app.core.config import get_settings

        s = get_settings()
        _known_cache = [
            v for v in (
                s.gemini_api_key, s.openrouter_api_key, s.hosted_api_key,
                s.tavily_api_key, s.knowledge_base_api_key, s.app_secret,
            )
            # Short values would redact innocent text; a real key is long.
            if v and len(v) >= 12
        ]
    return _known_cache


def reset_cache() -> None:
    """For tests that change settings."""
    global _known_cache
    _known_cache = None


def redact_text(text: str) -> str:
    for secret in _known_secrets():
        text = text.replace(secret, REDACTED)
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def redact(value: Any) -> Any:
    """Walk any JSON-ish structure and redact strings.

    Dict KEYS are left alone: a key named `api_key` is not itself a secret, and
    rewriting keys would corrupt the trace's shape for no gain. The value under
    it is redacted like any other string.
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value
