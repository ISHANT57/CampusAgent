"""Application settings — the single source of truth for every tunable.

Fail-fast is the point. An agent holds four credentials (Gemini, OpenRouter,
Project 1, Tavily) and makes 10-15 LLM calls per run. Discovering a missing key
at step 7 means six calls already spent, a half-written trace, and an error
message pointing at the HTTP layer rather than the config. Pydantic validates
everything at import, so the process refuses to start instead.

Anything without a default is REQUIRED. That is deliberate: a secret with a
default is a secret that silently works in dev and silently fails in prod.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["gemini", "openrouter"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Ignore unrelated variables in the environment rather than erroring.
        # Render injects its own (RENDER_*, PORT); a strict setting would make
        # the app refuse to boot on the platform it deploys to.
        extra="ignore",
    )

    # -- Database ------------------------------------------------------------
    # Scheme must be postgresql+psycopg:// (psycopg3). psycopg2 has no wheel on
    # modern Python and the two drivers are not interchangeable in the URL.
    # A SEPARATE database from Project 1's — two services, two datastores.
    database_url: str

    # -- LLM routing ---------------------------------------------------------
    # Frozen by the M0 evaluation. These are config, not constants, precisely
    # so the M0 finding can be applied (or reversed) without a code change —
    # which is the whole claim the provider abstraction makes.
    llm_primary_provider: ProviderName = "gemini"
    llm_fallback_provider: ProviderName = "openrouter"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-oss-20b:free"
    # OpenRouter routes one model name across upstream hosts with different
    # quantisations. Unpinned, two identical requests can hit two different
    # machines — so measured reliability describes the routing lottery, not the
    # model. Off by default; see spike/PROVIDER_EVALUATION.md.
    openrouter_allow_fallbacks: bool = False

    llm_timeout: float = 90.0
    llm_max_output_tokens: int = 1024

    # -- Project 1 (Knowledge Service) ---------------------------------------
    # Consumed over HTTP only. P2 never touches P1's Postgres, Qdrant, or
    # object storage.
    #
    # No default URL. A default would be a hardcoded deployment target, and the
    # environment (local / staging / production) must be a configuration
    # choice, not a code choice. Missing = the app refuses to start, which is
    # better than silently pointing at the wrong knowledge base.
    knowledge_base_url: str
    knowledge_base_api_key: str = ""

    # Generous because Project 1 runs on Render's free tier, which spins down
    # after idle and can take ~50s to cold-start. A 20s timeout would make the
    # first search of the day fail every time. Paired with one retry in the
    # client, worst case is ~62s.
    knowledge_timeout: float = 30.0

    # -- Tools ---------------------------------------------------------------
    tavily_api_key: str = ""

    # -- Run budget (M19) ----------------------------------------------------
    # Calibrated from M0's measured token and latency profile. Agent cost grows
    # super-linearly with step count: both the trace and the tool schemas are
    # re-sent on every turn.
    max_steps: int = Field(default=15, ge=1, le=100)
    max_tokens_per_run: int = Field(default=120_000, ge=1000)
    max_wall_clock_seconds: int = Field(default=300, ge=10)
    max_calls_per_tool: int = Field(default=4, ge=1)

    # -- Hosted trial --------------------------------------------------------
    # Reachable ONLY through Mode.TRIAL in llm/manager.py. Rule 2 of
    # PROVIDER_ARCHITECTURE.md: a BYOK failure never falls back to these.
    #
    # Free -> paid is these values plus the trial limits below. No code change:
    # the adapter is chosen from catalogue.json by provider name.
    hosted_provider: str = "gemini"          # gemini | openrouter | groq
    hosted_model: str = ""                   # blank = the catalogue default
    # SEPARATE key from the development one. Quotas are per-model PER KEY
    # (M0/F6), so a shared key means a demo day exhausts what you build with —
    # and you find out mid-run.
    hosted_api_key: str = ""

    # -- Trial limits (raise these when upgrading to a paid hosted plan) ------
    trial_runs_per_identity: int = Field(default=2, ge=0)
    trial_runs_per_ip: int = Field(default=20, ge=0)
    # Keep BELOW the vendor's own limit, so we hit OUR ceiling first and fail
    # with a message we wrote rather than a 429 mid-run.
    trial_global_daily_runs: int = Field(default=100, ge=0)
    trial_max_steps: int = Field(default=4, ge=1)
    trial_max_tokens: int = Field(default=25_000, ge=1000)
    # web_search also costs Tavily quota (1,000/month free). knowledge_search
    # and calculator are also the honest demo — they are what makes this
    # visibly different from a chatbot.
    trial_tools: str = "knowledge_search,calculator"

    # -- BYOK ----------------------------------------------------------------
    byok_session_ttl_minutes: int = 120
    byok_allow_custom_base_url: bool = True
    # Ollama needs http://localhost:11434. Safe only when the app runs on the
    # user's own machine — in a hosted deploy there is no user machine, so this
    # MUST stay false in production. See llm/url_guard.py.
    byok_allow_loopback: bool = False

    @property
    def trial_tool_names(self) -> list[str]:
        return [t.strip() for t in self.trial_tools.split(",") if t.strip()]

    # -- Tenancy -------------------------------------------------------------
    # Single-tenant today. tenant_id is carried through every table and query
    # from M4 so multi-tenancy later changes resolve_tenant() and nothing else.
    # Project 1 put its equivalent constant at the endpoint instead, which is
    # why it cannot serve a second institution without touching that endpoint.
    default_tenant_id: int = 1

    # -- App -----------------------------------------------------------------
    cors_allowed_origins: str = "*"
    log_level: str = "INFO"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @field_validator("database_url")
    @classmethod
    def _check_driver(cls, v: str) -> str:
        # Catches the most likely copy-paste error from Project 1's config,
        # where the psycopg2 URL is correct. Failing here with a clear message
        # beats a ModuleNotFoundError from deep inside SQLAlchemy's dialect
        # loader, which does not mention the URL at all.
        if v.startswith("postgresql+psycopg2://"):
            raise ValueError(
                "DATABASE_URL uses psycopg2, but this project installs psycopg3. "
                "Change the scheme to postgresql+psycopg://"
            )
        return v

    @model_validator(mode="after")
    def _require_active_provider_keys(self) -> "Settings":
        """Require a key for whichever providers are actually in use.

        Checked here rather than per-field because which key is mandatory
        depends on other settings. Making both keys unconditionally required
        would block anyone running with a single provider; making neither
        required would defer the failure to the first LLM call — the exact
        fail-late behaviour this module exists to prevent.
        """
        needed = {self.llm_primary_provider, self.llm_fallback_provider}
        missing = [
            f"{name.upper()}_API_KEY"
            for name in needed
            if not getattr(self, f"{name}_api_key")
        ]
        if missing:
            raise ValueError(
                f"Missing required credential(s): {', '.join(missing)}. "
                f"Active providers: primary={self.llm_primary_provider}, "
                f"fallback={self.llm_fallback_provider}."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached accessor. Import this, not a module-level instance.

    A module-level `settings = Settings()` (Project 1's approach) evaluates at
    import time, which means tests cannot construct a Settings with different
    values without reaching into the module. lru_cache gives the same
    single-instance behaviour while leaving a seam: tests call
    get_settings.cache_clear().
    """
    return Settings()  # pyright: ignore[reportCallIssue]
