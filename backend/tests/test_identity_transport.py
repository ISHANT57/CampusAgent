"""Identity via header and query param, NOT just cookie.

The reason this exists: the frontend and API are different sites, so a cookie
must be SameSite=None (third-party), which many browser profiles block. These
tests prove a run is owned correctly when the identity travels as a header
(fetch) or a query param (EventSource), with no cookie at all.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.identity import issue
from app.main import app


@pytest.fixture(autouse=True)
def _reset_limiter():
    from app.core.rate_limit import limiter
    limiter.reset()
    yield
    limiter.reset()


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


def test_identity_endpoint_issues_a_token():
    body = TestClient(app).get("/api/v1/identity").json()
    assert body["token"] and body["token"].count(".") == 2


def test_the_same_token_is_returned_when_presented_again():
    token = issue().token
    body = TestClient(app).get("/api/v1/identity", headers={"X-Identity": token}).json()
    assert body["token"] == token


def test_a_run_is_owned_via_header_with_no_cookie(fake_resolution):
    # The whole point: no cookie anywhere in this exchange.
    token = TestClient(app).get("/api/v1/identity").json()["token"]
    h = {"X-Identity": token}

    c = TestClient(app)
    run_id = c.post("/api/v1/runs", json={"goal": "header-owned"}, headers=h).json()["run_id"]
    # A different client with the SAME header sees it; without the header does not.
    assert TestClient(app).get(f"/api/v1/runs/{run_id}", headers=h).status_code == 200
    assert TestClient(app).get(f"/api/v1/runs/{run_id}").status_code == 404


def test_sse_is_authorised_by_the_query_param(fake_resolution):
    # EventSource cannot send headers, so the token rides in ?token=.
    token = TestClient(app).get("/api/v1/identity").json()["token"]
    h = {"X-Identity": token}
    run_id = TestClient(app).post("/api/v1/runs", json={"goal": "sse-token"}, headers=h).json()["run_id"]

    # Finish the run so the stream terminates instead of polling forever.
    from app.core.database import SessionLocal
    from app.models.run import RunStatus
    from app.repositories.run_repository import UNSCOPED, RunRepository
    db = SessionLocal()
    try:
        repo = RunRepository(db, identity=UNSCOPED)
        repo.finish(repo.get(run_id), RunStatus.COMPLETED, answer="ok")
    finally:
        db.close()

    # With the query token: authorised, streams, and closes on `done`.
    with TestClient(app).stream("GET", f"/api/v1/runs/{run_id}/events?token={token}") as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert "event: done" in body
    # Without it: 404, same as any non-owner.
    assert TestClient(app).get(f"/api/v1/runs/{run_id}/events").status_code == 404


def test_header_takes_priority_over_a_stale_cookie(fake_resolution):
    a, b = issue().token, issue().token
    # Own a run under identity A (via header).
    run_id = TestClient(app).post(
        "/api/v1/runs", json={"goal": "priority"}, headers={"X-Identity": a}
    ).json()["run_id"]
    # A request carrying B's cookie but A's header must resolve to A.
    c = TestClient(app)
    c.cookies.set("cb_identity", b)
    assert c.get(f"/api/v1/runs/{run_id}", headers={"X-Identity": a}).status_code == 200
