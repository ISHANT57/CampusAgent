"""F0-F5: scoped repository, run list, run metrics, providers, cancel."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.repositories.run_repository import UNSCOPED, RunRepository


@pytest.fixture(autouse=True)
def _reset_limiter():
    from app.core.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def fake_resolution(monkeypatch):
    from app.core.budget import RunBudget
    from app.llm.manager import Mode, ResolvedProvider

    class FakeProvider:
        name, model = "fake", "fake-1"

    resolved = ResolvedProvider(
        provider=FakeProvider(), mode=Mode.BYOK,
        budget=RunBudget(max_steps=4, max_wall_clock_seconds=10),
        label="Fake", provider_name="fake", model="fake-1",
    )
    monkeypatch.setattr("app.api.v1.runs.resolve", lambda ctx, **kw: resolved)
    monkeypatch.setattr("app.api.v1.runs._execute_in_background", lambda *a, **k: None)
    return resolved


# --- F0: ownership enforced by the repository -------------------------------

def test_identity_is_a_required_argument():
    """The whole point of F0.

    Ownership used to be a guard each endpoint had to remember, and the next
    one could forget — which is exactly how the IDOR happened. Forgetting is
    now a TypeError at import, not a disclosure in production.
    """
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        with pytest.raises(TypeError):
            RunRepository(db)          # type: ignore[call-arg]
    finally:
        db.close()


def test_scoped_repository_hides_another_identity(monkeypatch):
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        theirs = RunRepository(db, identity=UNSCOPED).create("theirs", identity="someone-else")
        assert RunRepository(db, identity="me").get(theirs.id) is None
        assert RunRepository(db, identity=UNSCOPED).get(theirs.id) is not None
    finally:
        db.close()


def test_recent_and_count_are_scoped():
    # recent() was the loaded gun: unexposed before, and it would have leaked
    # everything the moment a list endpoint existed.
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        seed = RunRepository(db, identity=UNSCOPED)
        seed.create("a-1", identity="ident-a")
        seed.create("b-1", identity="ident-b")

        a = RunRepository(db, identity="ident-a")
        assert all(r.identity == "ident-a" for r in a.recent(50))
        assert a.count() >= 1
        assert RunRepository(db, identity="nobody-at-all").count() == 0
    finally:
        db.close()


# --- F1: GET /runs ----------------------------------------------------------

def test_list_returns_only_my_runs(client, fake_resolution):
    client.post("/api/v1/runs", json={"goal": "mine one"})
    client.post("/api/v1/runs", json={"goal": "mine two"})

    goals = [r["goal"] for r in client.get("/api/v1/runs").json()["runs"]]
    assert "mine one" in goals and "mine two" in goals

    other = TestClient(app).get("/api/v1/runs").json()["runs"]
    other_goals = [r["goal"] for r in other]
    assert "mine one" not in other_goals and "mine two" not in other_goals


def test_list_is_newest_first_and_paginated(client, fake_resolution):
    for i in range(3):
        client.post("/api/v1/runs", json={"goal": f"paged {i}"})
    body = client.get("/api/v1/runs?limit=2").json()
    assert len(body["runs"]) == 2
    assert body["runs"][0]["run_id"] > body["runs"][1]["run_id"]
    assert body["total"] >= 3


def test_list_carries_no_step_payloads(client, fake_resolution):
    # A list of 20 runs with full traces would be megabytes, and the list view
    # renders none of it.
    client.post("/api/v1/runs", json={"goal": "no steps here"})
    assert "steps" not in client.get("/api/v1/runs").json()["runs"][0]


# --- F2: metrics ------------------------------------------------------------

def test_run_view_exposes_metrics(client, fake_resolution):
    run_id = client.post("/api/v1/runs", json={"goal": "metrics"}).json()["run_id"]
    body = client.get(f"/api/v1/runs/{run_id}").json()
    for field in ("step_count", "prompt_tokens", "completion_tokens", "created_at"):
        assert field in body, field


# --- F3: GET /providers -----------------------------------------------------

def test_provider_catalogue_is_served(client):
    body = client.get("/api/v1/providers").json()
    assert {"gemini", "groq", "openrouter", "ollama"} <= {p["id"] for p in body}
    assert all(p["label"] and p["blurb"] for p in body)


def test_catalogue_never_returns_a_credential(client):
    from app.core.config import get_settings

    text = client.get("/api/v1/providers").text
    s = get_settings()
    for secret in (s.gemini_api_key, s.hosted_api_key, s.openrouter_api_key):
        if secret:
            assert secret not in text


def test_models_without_tool_support_are_not_offered(client):
    # A model that cannot call tools produces an agent that appears to refuse
    # every task, with no error anywhere (M0/E2).
    for p in client.get("/api/v1/providers").json():
        assert all(m["supports_tools"] for m in p["models"])


def test_ollama_needs_no_key(client):
    ollama = next(p for p in client.get("/api/v1/providers").json() if p["id"] == "ollama")
    assert ollama["requires_key"] is False


# --- F4: POST /providers/test ----------------------------------------------

def test_connection_test_reports_a_missing_key_cleanly(client):
    body = client.post("/api/v1/providers/test",
                       json={"provider": "groq", "api_key": ""}).json()
    assert body["ok"] is False and body["reason"] == "missing_key"


def test_connection_test_rejects_an_ssrf_base_url(client):
    body = client.post("/api/v1/providers/test", json={
        "provider": "custom", "api_key": "k", "model": "m",
        "base_url": "http://169.254.169.254/v1",
    }).json()
    assert body["reason"] == "unsafe_base_url"


def test_connection_test_flags_a_model_that_ignores_tools(client, monkeypatch):
    """The check that prevents the worst support case.

    M0/E2 found models that accept a `tools` parameter and silently ignore it.
    Caught here it is a setup message; missed, it is an agent that appears to
    refuse every task at step 4 of a real run.
    """
    from app.llm.base import Completion

    class NoTools:
        name, model = "fake", "fake-1"

        def complete(self, messages, tools=None, temperature=0.0):
            return Completion(text="sure", model="fake-1", latency_ms=10)

    monkeypatch.setattr("app.api.v1.providers.build_provider", lambda *a, **k: NoTools())
    body = client.post("/api/v1/providers/test",
                       json={"provider": "groq", "api_key": "k"}).json()
    assert body["ok"] is True
    assert body["tool_calling"] is False
    assert "will not work" in body["error"]


# --- F5: cancel -------------------------------------------------------------

def test_cancel_marks_a_running_run_cancelled(client, fake_resolution):
    from app.core.database import SessionLocal
    from app.models.run import RunStatus

    run_id = client.post("/api/v1/runs", json={"goal": "cancel me"}).json()["run_id"]
    db = SessionLocal()
    try:
        repo = RunRepository(db, identity=UNSCOPED)
        repo.start(repo.get(run_id))
    finally:
        db.close()

    body = client.post(f"/api/v1/runs/{run_id}/cancel").json()
    assert body["status"] == RunStatus.CANCELLED.value


def test_cancelling_a_finished_run_is_not_an_error(client, fake_resolution):
    # The client may simply have raced the final step.
    from app.core.database import SessionLocal
    from app.models.run import RunStatus

    run_id = client.post("/api/v1/runs", json={"goal": "already done"}).json()["run_id"]
    db = SessionLocal()
    try:
        repo = RunRepository(db, identity=UNSCOPED)
        repo.finish(repo.get(run_id), RunStatus.COMPLETED, answer="done")
    finally:
        db.close()

    r = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == RunStatus.COMPLETED.value


def test_cannot_cancel_another_browsers_run(client, fake_resolution):
    run_id = client.post("/api/v1/runs", json={"goal": "not yours"}).json()["run_id"]
    assert TestClient(app).post(f"/api/v1/runs/{run_id}/cancel").status_code == 404
