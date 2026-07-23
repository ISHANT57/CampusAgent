"""Provider Manager — decides which LLM serves a run.

THE ONLY MODULE THAT TOUCHES HOSTED KEYS. That is not a convention, it is the
security boundary: rule 2 of PROVIDER_ARCHITECTURE.md says a BYOK failure must
never fall back to a hosted key, and a single gated code path is what makes
that auditable rather than hoped for.

Everything above this file — loop.py, selector.py, every tool — receives an
LLMProvider and cannot tell whose key paid for it. Same discipline as
knowledge.py being the only file that knows Project 1 exists.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from enum import Enum

from app.core.budget import RunBudget
from app.core.config import get_settings
from app.llm.base import LLMProvider
from app.llm.gemini import GeminiProvider
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.url_guard import UnsafeProviderURL, validate_provider_url

CATALOGUE_PATH = pathlib.Path(__file__).parent / "catalogue.json"


class Mode(str, Enum):
    TRIAL = "trial"      # our key, strict limits, onboarding only
    BYOK = "byok"        # their key, their limits


class NoProviderAvailable(Exception):
    """Typed so the UI can render the right screen. `reason` distinguishes
    'you are out of trial runs' (a conversion moment) from 'your key is
    invalid' (a fix-it moment) — two very different messages."""

    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ByokConfig:
    """A user's own provider. Never persisted to runs/steps/logs."""

    provider: str
    api_key: str = ""
    model: str | None = None
    base_url: str | None = None       # custom endpoints and Ollama only


@dataclass(frozen=True)
class RunContext:
    """Who is asking, and with what.

    `identity` is the signed browser token's hash, used for trial quota. It is
    deliberately not a user id: trial mode requires no signup.
    """

    mode: Mode = Mode.TRIAL
    identity: str | None = None
    byok: ByokConfig | None = None


@dataclass
class ResolvedProvider:
    provider: LLMProvider
    mode: Mode
    budget: RunBudget
    label: str                        # "Gemini · flash-lite" — UI and trace
    provider_name: str
    model: str


# --- catalogue --------------------------------------------------------------

_catalogue: dict | None = None


def catalogue() -> dict:
    global _catalogue
    if _catalogue is None:
        _catalogue = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))["providers"]
    return _catalogue


def provider_entry(name: str) -> dict:
    entry = catalogue().get(name)
    if entry is None:
        raise NoProviderAvailable(
            f"Unknown provider {name!r}. Known: {', '.join(sorted(catalogue()))}.",
            reason="unknown_provider",
        )
    return entry


def supports_tools(provider: str, model: str) -> bool | None:
    """None means the model is not in the catalogue — a custom id, which must
    be verified by a live connection test rather than assumed (M0/E2: models
    that accept `tools` and silently ignore them)."""
    for entry in provider_entry(provider).get("models", []):
        if entry["id"] == model:
            return bool(entry.get("supports_tools"))
    return None


# --- construction -----------------------------------------------------------

def build_provider(
    name: str,
    *,
    api_key: str = "",
    model: str | None = None,
    base_url: str | None = None,
) -> LLMProvider:
    """Turn a catalogue entry plus a credential into a working provider.

    The adapter is chosen by data, not by an if-chain over provider names —
    which is what makes adding Together or Fireworks a catalogue edit.
    """
    entry = provider_entry(name)
    settings = get_settings()

    resolved_model = model or entry.get("default_model")
    if not resolved_model:
        raise NoProviderAvailable(f"No model specified for {name}.", reason="no_model")

    resolved_base = base_url or entry.get("base_url")
    if not resolved_base:
        raise NoProviderAvailable(f"No base URL for {name}.", reason="no_base_url")

    # SSRF guard. A custom base URL means WE make a server-side request to a
    # URL the user chose — see url_guard for why that is the sharpest risk in
    # this design.
    if base_url or entry.get("allows_custom_base_url"):
        try:
            validate_provider_url(
                resolved_base,
                allow_loopback=entry.get("requires_loopback", False)
                and settings.byok_allow_loopback,
            )
        except UnsafeProviderURL as e:
            raise NoProviderAvailable(str(e), reason="unsafe_base_url") from e

    if entry.get("requires_key") and not api_key:
        raise NoProviderAvailable(f"{entry['label']} requires an API key.", reason="missing_key")

    adapter = entry["adapter"]
    if adapter == "gemini":
        return GeminiProvider(api_key=api_key, model=resolved_model)
    if adapter == "openai_compatible":
        return OpenAICompatibleProvider(
            name=name,
            base_url=resolved_base,
            model=resolved_model,
            api_key=api_key,
            auth=entry.get("auth", "bearer"),
            timeout=settings.llm_timeout,
            max_output_tokens=settings.llm_max_output_tokens,
        )
    # `anthropic` lands here until its adapter exists. Failing loudly beats
    # silently routing Claude through an incompatible format.
    raise NoProviderAvailable(f"Adapter {adapter!r} is not implemented yet.", reason="no_adapter")


# --- resolution -------------------------------------------------------------

def resolve(context: RunContext, quota_check=None) -> ResolvedProvider:
    """Decide which provider serves this run. Called ONCE per run.

    Not once per call: a run that switches provider mid-way produces a trace
    where half the steps came from a different model, which is unreadable when
    debugging and makes token accounting meaningless.

    `quota_check` is injected (returns None if allowed, or a reason string) so
    this module stays free of quota storage concerns and stays unit-testable
    without a database.
    """
    settings = get_settings()

    # --- 1. BYOK ------------------------------------------------------------
    if context.byok is not None:
        cfg = context.byok
        provider = build_provider(
            cfg.provider, api_key=cfg.api_key, model=cfg.model, base_url=cfg.base_url
        )
        entry = provider_entry(cfg.provider)
        model = cfg.model or entry["default_model"]
        return ResolvedProvider(
            provider=provider,
            mode=Mode.BYOK,
            budget=RunBudget.from_settings(),
            label=f"{entry['label']} · {model}",
            provider_name=cfg.provider,
            model=model,
        )
        # NOTE: there is deliberately no `except` here that falls through to
        # the hosted branch. If their key is broken the caller sees the real
        # error. Silently spending our quota on their misconfiguration is how
        # a free trial becomes a bill — and it is rule 2.

    # --- 2. Trial (hosted) --------------------------------------------------
    if context.mode is Mode.TRIAL:
        if quota_check is not None:
            denial = quota_check(context.identity)
            if denial:
                raise NoProviderAvailable(denial, reason="trial_exhausted")

        if not settings.hosted_api_key:
            raise NoProviderAvailable(
                "The hosted trial is not configured on this deployment. "
                "Connect your own provider to continue.",
                reason="hosted_unconfigured",
            )

        provider = build_provider(
            settings.hosted_provider,
            api_key=settings.hosted_api_key,
            model=settings.hosted_model,
        )
        entry = provider_entry(settings.hosted_provider)
        model = settings.hosted_model or entry["default_model"]
        return ResolvedProvider(
            provider=provider,
            mode=Mode.TRIAL,
            # The reduced budget is attached HERE, not chosen by the loop.
            # The loop must not know it is running a trial.
            budget=RunBudget(
                max_steps=settings.trial_max_steps,
                max_wall_clock_seconds=float(settings.max_wall_clock_seconds),
            ),
            label=f"Trial · {entry['label']} · {model}",
            provider_name=settings.hosted_provider,
            model=model,
        )

    raise NoProviderAvailable(
        "No provider configured. Connect your own API key to continue.",
        reason="no_provider",
    )
