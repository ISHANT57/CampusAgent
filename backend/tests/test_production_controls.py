"""Redaction and reaper — the last two production controls."""
import pytest
from datetime import datetime, timedelta, timezone

from app.core.redaction import REDACTED, redact, redact_text, reset_cache


# SYNTHETIC. Same SHAPE as the real thing, none of them valid.
#
# The first version of this file used the project's actual keys as fixtures,
# and GitHub's push protection refused the commit. It was right to: a test
# fixture is committed, public, and permanent, and "it's only a test" is how
# credentials end up in git history. Shape is all these tests need.
@pytest.mark.parametrize("secret", [
    "sk-or-v1-" + "0" * 48,
    "AIza" + "B" * 35,
    "gsk_" + "C" * 48,
    "tvly-dev-" + "D" * 40,
    "sk-proj-" + "E" * 32,
    "ghp_" + "F" * 32,
])
def test_key_shapes_are_redacted(secret):
    out = redact_text(f"provider error: bad key {secret} rejected")
    assert secret not in out and REDACTED in out


def test_connection_string_password_is_redacted():
    out = redact_text("could not connect: postgresql://user:hunter2@host/db")
    assert "hunter2" not in out


def test_authorization_header_echo_is_redacted():
    out = redact_text("upstream said: Authorization: Bearer abcdefghijklmnopqrstuvwxyz123")
    assert "abcdefghijklmnopqrstuvwxyz123" not in out


def test_redaction_walks_nested_structures():
    google, groq = "AIza" + "B" * 35, "gsk_" + "C" * 48
    payload = {"meta": {"error": f"key {google} invalid"}, "items": [{"t": groq}]}
    out = redact(payload)
    assert google not in str(out) and groq not in str(out)


def test_ordinary_text_survives():
    # Over-redaction would make traces useless.
    text = "The minimum CGPA is 6.5 per document 9, page 1."
    assert redact_text(text) == text


def test_dict_keys_are_not_rewritten():
    # A key named api_key is not itself a secret; rewriting keys would corrupt
    # the trace shape for no gain.
    out = redact({"api_key": "sk-proj-abcdefghijklmnopqrstuvwxyz012345"})
    assert "api_key" in out and out["api_key"] == REDACTED


def test_configured_secrets_are_redacted_by_value(monkeypatch):
    from app.core.config import get_settings
    reset_cache()
    monkeypatch.setattr(get_settings(), "tavily_api_key", "an-unusual-shaped-token-12345")
    try:
        assert "an-unusual-shaped-token-12345" not in redact_text("leaked an-unusual-shaped-token-12345")
    finally:
        reset_cache()


def test_a_key_in_a_step_never_reaches_the_database():
    """The guarantee, end to end: redaction is on WRITE."""
    from app.core.database import SessionLocal
    from app.models.step import StepKind
    from app.repositories.run_repository import UNSCOPED, RunRepository

    marker = "sk-or-v1-MARKERvalue0123456789abcdefghijklmnop"
    db = SessionLocal()
    try:
        repo = RunRepository(db, identity=UNSCOPED)
        run = repo.create("redaction check", identity="test")
        repo.add_step(run, StepKind.OBSERVATION.value, tool_name="t",
                      output={"error": f"provider rejected {marker}"},
                      error=f"bad key {marker}")
        stored = repo.steps(run.id)[0]
        assert marker not in str(stored.output)
        assert marker not in str(stored.error)
    finally:
        db.close()


# --- reaper -----------------------------------------------------------------

def test_reaper_finishes_a_run_whose_heartbeat_went_stale():
    from app.core.database import SessionLocal
    from app.core.reaper import reap_stale_runs
    from app.models.run import RunStatus
    from app.repositories.run_repository import UNSCOPED, RunRepository

    db = SessionLocal()
    try:
        repo = RunRepository(db, identity=UNSCOPED)
        run = repo.create("abandoned", identity="test")
        repo.start(run)
        run.heartbeat_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.commit()

        assert reap_stale_runs(db) >= 1
        db.refresh(run)
        assert run.status == RunStatus.FAILED.value
        # It must say WHY: "running" forever is indistinguishable from thinking.
        assert "abandoned" in run.error.lower()
        assert run.finished_at is not None
    finally:
        db.close()


def test_reaper_leaves_a_live_run_alone():
    from app.core.database import SessionLocal
    from app.core.reaper import reap_stale_runs
    from app.models.run import RunStatus
    from app.repositories.run_repository import UNSCOPED, RunRepository

    db = SessionLocal()
    try:
        repo = RunRepository(db, identity=UNSCOPED)
        run = repo.create("still working", identity="test")
        repo.start(run)          # heartbeat is now
        reap_stale_runs(db)
        db.refresh(run)
        # Reaping a live run is worse than reaping a dead one late.
        assert run.status == RunStatus.RUNNING.value
    finally:
        db.close()


def test_reaper_catches_a_run_created_but_never_started():
    from app.core.database import SessionLocal
    from app.core.reaper import reap_stale_runs
    from app.models.run import RunStatus
    from app.repositories.run_repository import UNSCOPED, RunRepository

    db = SessionLocal()
    try:
        repo = RunRepository(db, identity=UNSCOPED)
        run = repo.create("never started", identity="test")   # no heartbeat at all
        run.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.commit()
        reap_stale_runs(db)
        db.refresh(run)
        assert run.status == RunStatus.FAILED.value
    finally:
        db.close()
