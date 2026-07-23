"""P1 tests — the Provider Manager, catalogue, and SSRF guard.

Two of these matter more than the rest:

  test_byok_failure_never_falls_back_to_hosted   — rule 2, and the rule most
      likely to be violated by accident, because "just make it work" is the
      tempting fix when a user's key fails and a hosted key is right there.

  the url_guard suite                             — the sharpest risk this
      feature introduces. A user-supplied base URL is a server-side request to
      an address they chose.
"""

import httpx
import pytest

from app.llm.base import LLMPermanentError, LLMParseError, LLMTransientError, Message, ToolSpec
from app.llm.manager import (
    ByokConfig,
    Mode,
    NoProviderAvailable,
    RunContext,
    build_provider,
    catalogue,
    provider_entry,
    resolve,
    supports_tools,
)
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.url_guard import UnsafeProviderURL, validate_provider_url


# --- catalogue: providers as data -------------------------------------------

def test_catalogue_loads_all_eight_providers():
    assert set(catalogue()) == {
        "gemini", "groq", "openrouter", "github_models",
        "openai", "anthropic", "ollama", "custom",
    }


def test_every_provider_declares_an_adapter_and_a_blurb():
    for name, entry in catalogue().items():
        assert entry["adapter"] in {"gemini", "openai_compatible", "anthropic"}, name
        assert entry["label"] and entry["blurb"], name


def test_every_model_declares_tool_support():
    # M0/E2: models that accept a `tools` parameter and silently ignore it make
    # the agent look like it is refusing every task, with no error anywhere.
    for name, entry in catalogue().items():
        for model in entry.get("models", []):
            assert "supports_tools" in model, f"{name}/{model['id']}"


def test_eight_providers_collapse_to_three_adapters():
    adapters = {e["adapter"] for e in catalogue().values()}
    assert len(adapters) == 3


def test_supports_tools_returns_none_for_an_unknown_model():
    # A custom id must be verified by a live connection test, not assumed.
    assert supports_tools("gemini", "gemini-3.1-flash-lite") is True
    assert supports_tools("gemini", "some-model-we-never-listed") is None


def test_unknown_provider_is_a_typed_refusal():
    with pytest.raises(NoProviderAvailable) as e:
        provider_entry("not-a-provider")
    assert e.value.reason == "unknown_provider"


# --- construction -----------------------------------------------------------

def test_openai_compatible_adapter_serves_six_providers():
    for name in ("groq", "openrouter", "github_models", "openai", "custom"):
        assert catalogue()[name]["adapter"] == "openai_compatible"
    assert catalogue()["ollama"]["adapter"] == "openai_compatible"


def test_build_uses_the_catalogue_default_model():
    p = build_provider("groq", api_key="k")
    assert p.model == catalogue()["groq"]["default_model"]
    assert isinstance(p, OpenAICompatibleProvider)


def test_missing_key_for_a_provider_that_needs_one():
    with pytest.raises(NoProviderAvailable) as e:
        build_provider("groq")
    assert e.value.reason == "missing_key"


def test_ollama_needs_no_key(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "byok_allow_loopback", True)
    p = build_provider("ollama")
    assert p.api_key == ""


def test_unimplemented_adapter_fails_loudly():
    # Better than silently routing Claude through an incompatible format.
    with pytest.raises(NoProviderAvailable) as e:
        build_provider("anthropic", api_key="k")
    assert e.value.reason == "no_adapter"


# --- resolution: the rules --------------------------------------------------

def test_byok_is_used_when_configured():
    r = resolve(RunContext(mode=Mode.BYOK, byok=ByokConfig(provider="groq", api_key="k")))
    assert r.mode is Mode.BYOK
    assert r.provider_name == "groq"
    assert "Groq" in r.label


def test_byok_failure_never_falls_back_to_hosted(monkeypatch):
    """RULE 2 — the one most likely to break by accident.

    A user's key being wrong must surface as their error. Silently spending our
    hosted quota on their misconfiguration is how a free trial becomes a bill.
    """
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "hosted_api_key", "OUR-HOSTED-KEY")
    monkeypatch.setattr(settings, "hosted_provider", "gemini")

    # BYOK with a provider that cannot be built at all.
    with pytest.raises(NoProviderAvailable) as e:
        resolve(RunContext(mode=Mode.BYOK, byok=ByokConfig(provider="groq", api_key="")))

    # It failed for THEIR reason, and never reached the hosted branch.
    assert e.value.reason == "missing_key"
    assert "hosted" not in str(e.value).lower()


def test_trial_uses_the_hosted_key_and_the_reduced_budget(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "hosted_api_key", "hosted")
    monkeypatch.setattr(settings, "hosted_provider", "gemini")
    monkeypatch.setattr(settings, "hosted_model", "gemini-3.1-flash-lite")
    monkeypatch.setattr(settings, "trial_max_steps", 4)

    r = resolve(RunContext(mode=Mode.TRIAL, identity="abc"))
    assert r.mode is Mode.TRIAL
    # The reduced budget is attached by the manager. The loop must not know it
    # is running a trial.
    assert r.budget.max_steps == 4
    assert r.label.startswith("Trial ·")


def test_trial_refuses_when_quota_is_gone(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "hosted_api_key", "hosted")
    with pytest.raises(NoProviderAvailable) as e:
        resolve(
            RunContext(mode=Mode.TRIAL, identity="abc"),
            quota_check=lambda identity: "You've used both trial runs for today.",
        )
    # A distinct reason, because this is the conversion moment — not an error.
    assert e.value.reason == "trial_exhausted"


def test_trial_refuses_cleanly_when_hosted_is_unconfigured(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "hosted_api_key", "")
    with pytest.raises(NoProviderAvailable) as e:
        resolve(RunContext(mode=Mode.TRIAL, identity="abc"))
    assert e.value.reason == "hosted_unconfigured"


def test_quota_is_not_consulted_for_byok():
    # BYOK costs us nothing, so it must not be gated on our quota store.
    called = {"n": 0}

    def spy(identity):
        called["n"] += 1
        return "denied"

    resolve(
        RunContext(mode=Mode.BYOK, byok=ByokConfig(provider="groq", api_key="k")),
        quota_check=spy,
    )
    assert called["n"] == 0


# --- SSRF guard -------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://evil.test/",
    "ftp://internal.test/",
])
def test_only_http_and_https(url):
    with pytest.raises(UnsafeProviderURL, match="http"):
        validate_provider_url(url)


@pytest.mark.parametrize("url,what", [
    ("http://127.0.0.1:11434/v1", "loopback"),
    ("http://localhost:8080/v1", "loopback by name"),
    ("http://10.0.0.5/v1", "private"),
    ("http://192.168.1.1/v1", "private"),
    ("http://169.254.169.254/latest/meta-data/", "CLOUD METADATA"),
    ("http://[::1]/v1", "IPv6 loopback"),
])
def test_non_public_provider_urls_are_rejected(url, what):
    with pytest.raises(UnsafeProviderURL):
        validate_provider_url(url)


def test_a_domain_pointing_at_metadata_is_rejected(monkeypatch):
    """Validating the STRING proves nothing.

    An attacker who controls a domain simply points its A record at
    169.254.169.254. Only the resolved address tells the truth.
    """
    monkeypatch.setattr(
        "app.llm.url_guard.socket.getaddrinfo",
        lambda h, p: [(2, 1, 6, "", ("169.254.169.254", 0))],
    )
    with pytest.raises(UnsafeProviderURL, match="not a public address"):
        validate_provider_url("https://my-llm-proxy.example.com/v1")


def test_loopback_allowed_only_when_explicitly_enabled():
    # Ollama's named exception — and the reason it must be off in production,
    # where there is no user machine for localhost to mean anything safe.
    with pytest.raises(UnsafeProviderURL):
        validate_provider_url("http://localhost:11434/v1", allow_loopback=False)
    assert validate_provider_url("http://localhost:11434/v1", allow_loopback=True)


def test_loopback_exception_does_not_open_private_ranges():
    # Allowing localhost must not also allow the internal network.
    with pytest.raises(UnsafeProviderURL):
        validate_provider_url("http://10.0.0.5/v1", allow_loopback=True)


def test_custom_base_url_is_validated_at_build_time():
    with pytest.raises(NoProviderAvailable) as e:
        build_provider("custom", api_key="k", model="m", base_url="http://169.254.169.254/v1")
    assert e.value.reason == "unsafe_base_url"


# --- the OpenAI-compatible adapter ------------------------------------------

def _provider(**kw) -> OpenAICompatibleProvider:
    defaults = dict(name="groq", base_url="https://api.groq.test/v1", model="m", api_key="k")
    return OpenAICompatibleProvider(**{**defaults, **kw})


def test_bearer_auth_header():
    assert _provider()._headers()["Authorization"] == "Bearer k"


def test_custom_auth_header():
    h = _provider(auth="header:x-api-key")._headers()
    assert h["x-api-key"] == "k" and "Authorization" not in h


def test_no_auth_sends_no_credential():
    h = _provider(auth="none", api_key="")._headers()
    assert "Authorization" not in h


def test_system_prompt_stays_a_message():
    # Unlike Gemini, which needs a separate systemInstruction field.
    body = _provider()._build_body(
        [Message(role="system", content="s"), Message(role="user", content="u")], None, 0.0
    )
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_tools_are_sent_as_json_schema_untranslated():
    spec = ToolSpec(name="t", description="d", parameters={"type": "object", "properties": {}})
    body = _provider()._build_body([Message(role="user", content="q")], [spec], 0.0)
    assert body["tools"][0]["function"]["parameters"] == spec.parameters


def test_string_arguments_are_parsed_to_a_dict():
    # THE difference from Gemini: this format returns a JSON string.
    raw = {"choices": [{"message": {"tool_calls": [
        {"id": "1", "type": "function",
         "function": {"name": "calculator", "arguments": '{"expression": "6.5 - 6.2"}'}}
    ]}, "finish_reason": "tool_calls"}]}
    _, calls, _ = _provider()._parse(raw)
    assert calls[0].arguments == {"expression": "6.5 - 6.2"}


def test_malformed_arguments_raise_a_parse_error_not_a_transient_one():
    # The fix is a repair prompt, not a retry — the provider is fine, the
    # output was not. A failure class impossible on Gemini's path.
    raw = {"choices": [{"message": {"tool_calls": [
        {"function": {"name": "calculator", "arguments": "{'expression': broken"}}
    ]}}]}
    with pytest.raises(LLMParseError):
        _provider()._parse(raw)


def test_object_arguments_are_accepted_despite_being_off_spec():
    raw = {"choices": [{"message": {"tool_calls": [
        {"function": {"name": "calculator", "arguments": {"expression": "1+1"}}}
    ]}}]}
    _, calls, _ = _provider()._parse(raw)
    assert calls[0].arguments == {"expression": "1+1"}


def _resp(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("POST", "https://x"))


def test_openrouter_daily_cap_is_permanent_not_transient():
    """M0/F2 and M0/F5 together.

    OpenRouter's daily cap arrives as a 429, but retrying before midnight UTC
    cannot succeed. Treating it as transient burns the run's whole budget.
    """
    with pytest.raises(LLMPermanentError):
        _provider()._raise_for_error(_resp(429, {"error": {
            "message": "Rate limit exceeded",
            "metadata": {"raw": "free-models-per-day. Add 10 credits to unlock 1000"},
        }}))


def test_ordinary_429_is_transient():
    with pytest.raises(LLMTransientError):
        _provider()._raise_for_error(_resp(429, {"error": {"message": "slow down"}}))


def test_invalid_key_is_permanent():
    with pytest.raises(LLMPermanentError):
        _provider()._raise_for_error(_resp(401, {"error": {"message": "Invalid API key provided"}}))


def test_server_error_is_transient():
    with pytest.raises(LLMTransientError):
        _provider()._raise_for_error(_resp(503, {"error": {"message": "overloaded"}}))


def test_error_carries_provider_and_model():
    with pytest.raises(LLMPermanentError) as e:
        _provider()._raise_for_error(_resp(401, {"error": {"message": "Invalid API key"}}))
    assert e.value.provider == "groq" and e.value.model == "m"
