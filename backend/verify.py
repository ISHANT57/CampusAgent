"""End-to-end verification of everything built so far.

    python verify.py

Checks the things pytest cannot: real config loading, live Neon connectivity,
actual schema shape, and the frozen M0 provider decision. Exits non-zero if
anything is wrong, so it works as a pre-commit or CI gate later.
"""

from __future__ import annotations

import sys

OK, BAD = "  [OK]  ", "  [FAIL]"
failures: list[str] = []


def check(label: str, fn) -> None:
    try:
        detail = fn()
        print(f"{OK} {label}" + (f" — {detail}" if detail else ""))
    except Exception as e:
        print(f"{BAD} {label} — {type(e).__name__}: {e}")
        failures.append(label)


print("\n=== M2  config ===")


def _config():
    from app.core.config import get_settings

    s = get_settings()
    assert s.database_url.startswith("postgresql+psycopg://"), "wrong DBAPI scheme"
    assert s.max_steps > 0
    return f"primary={s.llm_primary_provider}/{s.gemini_model}, max_steps={s.max_steps}"


check("settings load from .env", _config)


def _config_fails_loudly():
    from pydantic import ValidationError

    from app.core.config import Settings

    try:
        Settings(_env_file=None, gemini_api_key="k", openrouter_api_key="k")
    except ValidationError:
        return "missing DATABASE_URL rejected at startup"
    raise AssertionError("missing DATABASE_URL was NOT rejected")


check("missing required var fails fast", _config_fails_loudly)


print("\n=== M3  database ===")


def _connect():
    from sqlalchemy import text

    from app.core.database import engine

    with engine.connect() as c:
        v = c.execute(text("select version()")).scalar()
    return v.split(",")[0]


check("Neon reachable", _connect)


print("\n=== M4  schema ===")


def _schema():
    from sqlalchemy import inspect

    from app.core.database import engine

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for t in ("runs", "steps", "alembic_version"):
        assert t in tables, f"missing table {t}"

    run_cols = {c["name"] for c in insp.get_columns("runs")}
    step_cols = {c["name"] for c in insp.get_columns("steps")}
    for col in ("tenant_id", "status", "goal", "heartbeat_at", "plan_version"):
        assert col in run_cols, f"runs missing {col}"
    for col in ("run_id", "idx", "kind", "input", "output", "tenant_id"):
        assert col in step_cols, f"steps missing {col}"

    uniques = {u["name"] for u in insp.get_unique_constraints("steps")}
    assert "uq_steps_run_idx" in uniques, "steps(run_id, idx) uniqueness missing"
    return f"runs={len(run_cols)} cols, steps={len(step_cols)} cols, trace ordering enforced"


check("runs + steps tables correct", _schema)


def _write_read():
    """Round-trip a real row. Proves the ORM mapping matches the live schema —
    a migration can apply cleanly and still not match what the models expect."""
    from app.core.database import SessionLocal
    from app.models import Run, RunStatus, Step, StepKind

    db = SessionLocal()
    try:
        run = Run(goal="verify.py smoke test", status=RunStatus.CREATED.value)
        db.add(run)
        db.flush()
        db.add(Step(run_id=run.id, idx=0, kind=StepKind.THOUGHT.value, output={"text": "hello"}))
        db.commit()

        again = db.get(Run, run.id)
        assert again is not None and again.tenant_id == 1
        assert again.step_count == 0 and again.plan_version == 0

        db.delete(again)          # cascade removes the step
        db.commit()
        return f"run {run.id} written, read back, deleted (cascade)"
    finally:
        db.close()


check("ORM write/read/cascade round-trip", _write_read)


print("\n=== M5  api ===")


def _api():
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    assert c.get("/health").json() == {"status": "ok"}
    deps = c.get("/health/deps").json()
    assert deps["checks"]["database"]["status"] == "ok", deps
    return f"/health ok, /health/deps ok ({deps['checks']['database']['latency_ms']}ms)"


check("health endpoints", _api)


def _liveness_independent():
    """The single most important behaviour in M5: a dead database must NOT
    make the liveness probe fail, or a Neon blip becomes a restart loop."""
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from app.main import app

    with patch("app.api.v1.health.engine.connect", side_effect=OSError("down")):
        c = TestClient(app)
        assert c.get("/health").status_code == 200, "liveness died with the DB"
        assert c.get("/health/deps").json()["status"] == "degraded"
    return "liveness survives a dead database"


check("liveness independent of dependencies", _liveness_independent)


print("\n=== M6/M8  LLM provider (LIVE) ===")


def _protocol():
    from app.llm.base import LLMProvider
    from app.llm.gemini import GeminiProvider

    p = GeminiProvider()
    # Structural conformance — GeminiProvider does not inherit from anything.
    assert isinstance(p, LLMProvider), "GeminiProvider does not satisfy the Protocol"
    return f"{p.name}/{p.model} satisfies LLMProvider structurally"


check("Protocol conformance", _protocol)


def _live_text():
    from app.llm.base import Message
    from app.llm.gemini import GeminiProvider

    c = GeminiProvider().complete(
        [Message(role="system", content="You are terse."),
         Message(role="user", content="Reply with exactly the word: pong")]
    )
    assert c.text and "pong" in c.text.lower(), f"unexpected reply: {c.text!r}"
    assert c.usage.total > 0, "usage not parsed"
    return f"{c.latency_ms}ms, {c.usage.prompt_tokens}+{c.usage.completion_tokens} tokens"


check("live text completion", _live_text)


def _live_tool_call():
    """The claim the whole project rests on: the model reliably emits a
    parseable tool call with the right arguments."""
    from app.llm.base import Message, ToolSpec
    from app.llm.gemini import GeminiProvider

    tools = [
        ToolSpec(
            name="knowledge_search",
            description=(
                "Search the university's internal document corpus and return relevant "
                "passages. Use for questions about official policies, rules, curriculum, "
                "admissions or campus information. Returns fragments, not whole documents."
            ),
            parameters={
                "type": "object",
                "title": "Args",                    # Pydantic noise Gemini rejects
                "additionalProperties": False,      # ditto
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                    "top_k": {"type": "integer", "description": "How many passages."},
                },
                "required": ["query"],
            },
        ),
        ToolSpec(
            name="calculator",
            description="Evaluate one arithmetic or comparison expression, e.g. '6.5 - 6.2'.",
            parameters={
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "The expression."}},
                "required": ["expression"],
            },
        ),
    ]

    c = GeminiProvider().complete(
        [
            Message(role="system", content=(
                "You are an autonomous assistant for Sitare University students. "
                "Choose exactly ONE tool and call it."
            )),
            Message(role="user", content="What is the minimum CGPA I need to keep my scholarship?"),
        ],
        tools=tools,
    )

    call = c.tool_call
    assert call is not None, f"no tool call emitted; text={c.text!r}"
    assert call.name == "knowledge_search", f"wrong tool: {call.name}"
    assert isinstance(call.arguments, dict), "arguments not normalised to a dict"
    assert call.arguments.get("query"), f"required arg missing: {call.arguments}"
    return f"{call.name}({call.arguments}) in {c.latency_ms}ms"


check("live tool call, correct tool + args", _live_tool_call)


def _permanent_error_is_classified():
    """M0/F2 against the real API: a retired model must raise Permanent, not
    Transient — retrying it can never succeed."""
    from app.llm.base import LLMPermanentError
    from app.llm.gemini import GeminiProvider

    try:
        GeminiProvider(model="gemini-1.0-nonexistent").complete(
            [__import__("app.llm.base", fromlist=["Message"]).Message(role="user", content="hi")]
        )
    except LLMPermanentError as e:
        return f"classified permanent (status {e.status})"
    except Exception as e:
        raise AssertionError(f"expected LLMPermanentError, got {type(e).__name__}: {e}") from e
    raise AssertionError("a nonexistent model did not raise")


check("bad model -> LLMPermanentError", _permanent_error_is_classified)


print("\n=== M0  frozen decision ===")


def _m0():
    import pathlib

    p = pathlib.Path(__file__).parent.parent / "spike" / "PROVIDER_EVALUATION.md"
    assert p.exists(), "PROVIDER_EVALUATION.md missing"
    text = p.read_text(encoding="utf-8")
    assert "Provider decision FROZEN" in text

    from app.core.config import get_settings

    s = get_settings()
    # Amendment 1 promoted flash-lite over 2.5-flash on availability grounds.
    approved = {"gemini-3.1-flash-lite", "gemini-2.5-flash"}
    assert s.gemini_model in approved, f"config drifted from M0: {s.gemini_model}"
    return f"{s.gemini_model}, approved by PROVIDER_EVALUATION.md"


check("config matches the M0 decision", _m0)


print()
if failures:
    print(f"FAILED ({len(failures)}): {', '.join(failures)}")
    sys.exit(1)
print("ALL CHECKS PASSED")
