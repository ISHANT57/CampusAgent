"""M2's Definition of Done, as a runnable check.

The claim is "a missing required variable stops the app at startup with a
message naming it." An untested claim about failure behaviour is a hope — this
is the smallest thing that fails if the guarantee breaks.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

BASE = {
    # _env_file=None isolates these tests from the developer's real .env.
    # Without it, Settings() silently falls back to backend/.env, so the
    # "missing DATABASE_URL" test cannot actually make it missing — the test
    # passes on a machine with no .env and fails on one with it, which is the
    # worst kind of flake: it depends on untracked local state.
    "_env_file": None,
    "database_url": "postgresql+psycopg://u:p@h/db",
    "gemini_api_key": "k",
    "openrouter_api_key": "k",
    # Required with no default: a default would hardcode a deployment target.
    "knowledge_base_url": "https://p1.test",
    # Required with no default: a secret with a default works silently in dev
    # and is silently worthless in production.
    "app_secret": "test-secret",
}


def test_app_secret_is_required():
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as e:
        Settings(**{k: v for k, v in BASE.items() if k != "app_secret"})
    assert "app_secret" in str(e.value).lower()


def test_knowledge_base_url_is_required():
    """No default, deliberately. A default would silently point a misconfigured
    deploy at the wrong knowledge base — worse than refusing to start."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as e:
        Settings(**{k: v for k, v in BASE.items() if k != "knowledge_base_url"})
    assert "knowledge_base_url" in str(e.value).lower()


def test_valid_settings_load():
    s = Settings(**BASE)
    assert s.default_tenant_id == 1
    assert s.max_steps == 15


def test_missing_database_url_fails_at_construction():
    with pytest.raises(ValidationError) as e:
        Settings(**{k: v for k, v in BASE.items() if k != "database_url"})
    assert "database_url" in str(e.value).lower()


def test_psycopg2_url_is_rejected_with_a_useful_message():
    # The likeliest copy-paste error: Project 1's URL, which is correct there.
    with pytest.raises(ValidationError) as e:
        Settings(**{**BASE, "database_url": "postgresql+psycopg2://u:p@h/db"})
    assert "psycopg://" in str(e.value)


def test_missing_key_for_an_active_provider_is_rejected():
    with pytest.raises(ValidationError) as e:
        Settings(**{**BASE, "gemini_api_key": ""})
    assert "GEMINI_API_KEY" in str(e.value)


def test_key_is_not_required_for_an_inactive_provider():
    # Both roles on one provider: the other's key is genuinely unnecessary,
    # and demanding it would block single-provider runs for no reason.
    s = Settings(
        **{**BASE, "gemini_api_key": ""},
        llm_primary_provider="openrouter",
        llm_fallback_provider="openrouter",
    )
    assert s.llm_primary_provider == "openrouter"


def test_budget_bounds_are_enforced():
    with pytest.raises(ValidationError):
        Settings(**BASE, max_steps=0)


def test_cors_origins_splits():
    s = Settings(**BASE, cors_allowed_origins="https://a.com, https://b.com")
    assert s.cors_origins == ["https://a.com", "https://b.com"]
