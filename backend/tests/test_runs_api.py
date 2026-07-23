"""P2 tests — identity, the runs API, and SSE replay.

No network: the provider is stubbed at resolution, so these exercise the HTTP
contract rather than the agent.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.core import identity as ident
from app.core.identity import COOKIE_NAME, InvalidIdentity, issue, resolve_or_issue, verify
from app.main import app


# --- identity ---------------------------------------------------------------

def test_issued_token_verifies():
    i = issue()
    assert verify(i.token).subject == i.subject


def test_quota_key_is_a_hash_not_the_token():
    # The key lands in the database and in logs. A value replayable as a
    # credential does not belong in either.
    i = issue()
    assert i.subject not in i.key
    assert i.token not in i.key
    assert len(i.key) == 32


@pytest.mark.parametrize("bad", [
    None, "", "garbage", "a.b", "a.b.c.d",
    "subject.notanumber.sig",
])
def test_malformed_tokens_are_rejected(bad):
    with pytest.raises(InvalidIdentity):
        verify(bad)


def test_a_forged_signature_is_rejected():
    i = issue()
    subject, issued, _ = i.token.split(".")
    with pytest.raises(InvalidIdentity, match="signature"):
        verify(f"{subject}.{issued}.{'0' * 32}")


def test_tampering_with_the_issue_date_is_rejected():
    # Otherwise an old token could be back-dated to dodge expiry, or
    # forward-dated to look fresh.
    i = issue()
    subject, _, sig = i.token.split(".")
    with pytest.raises(InvalidIdentity, match="signature"):
        verify(f"{subject}.{int(time.time()) + 999}.{sig}")


def test_expired_token_is_rejected(monkeypatch):
    i = issue()
    monkeypatch.setattr(ident, "MAX_AGE_SECONDS", -1)
    with pytest.raises(InvalidIdentity, match="expired"):
        verify(i.token)


def test_a_bad_token_yields_a_new_identity_rather_than_an_error():
    # This is not authentication. Refusing service because a cookie was mangled
    # punishes an ordinary user for a browser quirk, and the attacker it would
    # inconvenience can simply clear the cookie.
    got, is_new = resolve_or_issue("tampered.999.badsig")
    assert is_new and verify(got.token)


def test_ip_hash_is_salted_and_not_reversible():
    h = ident.hash_ip("203.0.113.5")
    assert h and "203.0.113" not in h
    assert ident.hash_ip(None) is None


# --- API --------------------------------------------------------------------

@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def fake_resolution(monkeypatch):
    """Stub provider resolution so the API can be tested without a model."""
    from app.core.budget import RunBudget
    from app.llm.manager import Mode, ResolvedProvider

    class FakeProvider:
        name, model = "fake", "fake-1"

        def complete(self, messages, tools=None, temperature=0.0):
            raise AssertionError("the background task should not run in these tests")

    resolved = ResolvedProvider(
        provider=FakeProvider(), mode=Mode.TRIAL,
        budget=RunBudget(max_steps=4, max_wall_clock_seconds=10),
        label="Trial · Fake", provider_name="fake", model="fake-1",
    )
    monkeypatch.setattr("app.api.v1.runs.resolve", lambda ctx, **kw: resolved)
    # Neutralise the background task: these tests are about the HTTP contract.
    monkeypatch.setattr("app.api.v1.runs._execute_in_background", lambda *a, **k: None)
    return resolved


def test_post_run_returns_202_with_an_id(client, fake_resolution):
    r = client.post("/api/v1/runs", json={"goal": "What is 2 plus 2?"})
    assert r.status_code == 202
    body = r.json()
    assert body["run_id"] > 0
    assert body["mode"] == "trial"
    # The client learns which provider served it without a second request.
    assert body["provider"] == "fake" and body["model"] == "fake-1"


def test_identity_cookie_is_set_on_first_contact_and_reused(client, fake_resolution):
    first = client.post("/api/v1/runs", json={"goal": "one"})
    assert COOKIE_NAME in first.cookies

    token = first.cookies[COOKIE_NAME]
    second = client.post("/api/v1/runs", json={"goal": "two"})
    # Same browser, same identity — otherwise trial quota counts nothing.
    assert COOKIE_NAME not in second.cookies or second.cookies[COOKIE_NAME] == token


def test_identity_cookie_is_httponly(client, fake_resolution):
    r = client.post("/api/v1/runs", json={"goal": "x"})
    assert "httponly" in r.headers["set-cookie"].lower()


def test_trial_exhaustion_is_429_with_a_machine_readable_reason(client, monkeypatch):
    from app.llm.manager import NoProviderAvailable

    def refuse(ctx, **kw):
        raise NoProviderAvailable("You've used both trial runs for today.",
                                  reason="trial_exhausted")

    monkeypatch.setattr("app.api.v1.runs.resolve", refuse)
    r = client.post("/api/v1/runs", json={"goal": "x"})
    assert r.status_code == 429
    # The reason lets the UI show a conversion screen rather than an error.
    assert r.json()["detail"]["reason"] == "trial_exhausted"


def test_a_bad_byok_key_is_400_not_500(client, monkeypatch):
    from app.llm.manager import NoProviderAvailable

    def refuse(ctx, **kw):
        raise NoProviderAvailable("Groq requires an API key.", reason="missing_key")

    monkeypatch.setattr("app.api.v1.runs.resolve", refuse)
    r = client.post("/api/v1/runs", json={"goal": "x", "byok": {"provider": "groq"}})
    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == "missing_key"


def test_refusals_do_not_create_a_run(client, monkeypatch):
    """A refusal is not a failed run.

    Recording one would corrupt the success-rate metric with attempts the agent
    never made.
    """
    from app.core.database import SessionLocal
    from app.llm.manager import NoProviderAvailable
    from app.repositories.run_repository import RunRepository

    db = SessionLocal()
    try:
        before = len(RunRepository(db).recent(200))
    finally:
        db.close()

    monkeypatch.setattr(
        "app.api.v1.runs.resolve",
        lambda ctx, **kw: (_ for _ in ()).throw(
            NoProviderAvailable("nope", reason="trial_exhausted")
        ),
    )
    client.post("/api/v1/runs", json={"goal": "should not persist"})

    db = SessionLocal()
    try:
        assert len(RunRepository(db).recent(200)) == before
    finally:
        db.close()


def test_goal_is_bounded(client, fake_resolution):
    assert client.post("/api/v1/runs", json={"goal": ""}).status_code == 422
    assert client.post("/api/v1/runs", json={"goal": "x" * 5000}).status_code == 422


def test_get_run_404s_for_an_unknown_id(client):
    assert client.get("/api/v1/runs/99999999").status_code == 404


def test_get_run_returns_the_trace(client, fake_resolution):
    run_id = client.post("/api/v1/runs", json={"goal": "trace me"}).json()["run_id"]
    body = client.get(f"/api/v1/runs/{run_id}").json()
    assert body["run_id"] == run_id
    assert body["goal"] == "trace me"
    assert isinstance(body["steps"], list)


# --- SSE --------------------------------------------------------------------

def test_sse_streams_and_terminates_on_a_finished_run(client, fake_resolution):
    from app.core.database import SessionLocal
    from app.models.run import RunStatus
    from app.models.step import StepKind
    from app.repositories.run_repository import RunRepository

    run_id = client.post("/api/v1/runs", json={"goal": "streamed"}).json()["run_id"]

    db = SessionLocal()
    try:
        repo = RunRepository(db)
        run = repo.get(run_id)
        repo.add_step(run, StepKind.THOUGHT.value, output={"text": "thinking"})
        repo.add_step(run, StepKind.FINAL.value, output={"answer": "done"})
        repo.finish(run, RunStatus.COMPLETED, answer="done")
    finally:
        db.close()

    with client.stream("GET", f"/api/v1/runs/{run_id}/events") as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        body = "".join(r.iter_text())

    assert "event: run" in body
    assert "event: step" in body
    assert "event: done" in body
    assert "id: 0" in body            # event ids are step indexes


def test_sse_resumes_from_last_event_id(client, fake_resolution):
    """Reconnection is a `WHERE idx > n` query.

    No replay buffer and no server-side stream state — the durable trace IS the
    buffer, which is only true because the loop commits every step.
    """
    from app.core.database import SessionLocal
    from app.models.run import RunStatus
    from app.models.step import StepKind
    from app.repositories.run_repository import RunRepository

    run_id = client.post("/api/v1/runs", json={"goal": "resumed"}).json()["run_id"]

    db = SessionLocal()
    try:
        repo = RunRepository(db)
        run = repo.get(run_id)
        for i in range(4):
            repo.add_step(run, StepKind.THOUGHT.value, output={"text": f"step {i}"})
        repo.finish(run, RunStatus.COMPLETED, answer="ok")
    finally:
        db.close()

    with client.stream("GET", f"/api/v1/runs/{run_id}/events",
                       headers={"Last-Event-ID": "1"}) as r:
        body = "".join(r.iter_text())

    assert "step 0" not in body and "step 1" not in body     # already delivered
    assert "step 2" in body and "step 3" in body             # the remainder


def test_sse_summarises_observations_instead_of_streaming_them_whole(client, fake_resolution):
    """A live run exposed this: one knowledge_search observation was ~5KB.

    Every retrieved passage, plus a `rendered` duplicate of the same text for
    the prompt. Streaming that pushes kilobytes per step to a browser drawing
    one line, and duplicates what GET /runs/{id} already serves in full.
    """
    from app.core.database import SessionLocal
    from app.models.run import RunStatus
    from app.models.step import StepKind
    from app.repositories.run_repository import RunRepository

    big = "PASSAGE " * 2000
    run_id = client.post("/api/v1/runs", json={"goal": "big"}).json()["run_id"]

    db = SessionLocal()
    try:
        repo = RunRepository(db)
        run = repo.get(run_id)
        repo.add_step(
            run, StepKind.OBSERVATION.value, tool_name="knowledge_search",
            output={"ok": True, "unavailable": False, "data": [{"text": big}],
                    "meta": {"count": 5, "rendered": big, "latency_ms": 42}},
        )
        repo.finish(run, RunStatus.COMPLETED, answer="ok")
    finally:
        db.close()

    with client.stream("GET", f"/api/v1/runs/{run_id}/events") as r:
        body = "".join(r.iter_text())

    assert len(body) < 4000, "the stream is carrying the whole observation"
    assert '"truncated": true' in body
    # The useful summary survives: outcome, count, latency, a preview.
    assert '"count": 5' in body and '"ok": true' in body and '"latency_ms": 42' in body

    # ...and the FULL payload is still available from the run endpoint.
    full = client.get(f"/api/v1/runs/{run_id}").json()
    assert len(str(full["steps"])) > 10_000


def test_sse_tool_call_events_carry_their_arguments(client, fake_resolution):
    from app.core.database import SessionLocal
    from app.models.run import RunStatus
    from app.models.step import StepKind
    from app.repositories.run_repository import RunRepository

    run_id = client.post("/api/v1/runs", json={"goal": "args"}).json()["run_id"]
    db = SessionLocal()
    try:
        repo = RunRepository(db)
        run = repo.get(run_id)
        repo.add_step(run, StepKind.TOOL_CALL.value, tool_name="calculator",
                      input={"expression": "6.5 - 6.2"})
        repo.finish(run, RunStatus.COMPLETED, answer="0.3")
    finally:
        db.close()

    with client.stream("GET", f"/api/v1/runs/{run_id}/events") as r:
        body = "".join(r.iter_text())
    assert "6.5 - 6.2" in body


def test_sse_on_an_unknown_run_reports_and_closes(client):
    with client.stream("GET", "/api/v1/runs/99999999/events") as r:
        body = "".join(r.iter_text())
    assert "event: error" in body and "not found" in body.lower()


def test_sse_sets_headers_that_stop_proxies_buffering(client, fake_resolution):
    from app.core.database import SessionLocal
    from app.models.run import RunStatus
    from app.repositories.run_repository import RunRepository

    run_id = client.post("/api/v1/runs", json={"goal": "headers"}).json()["run_id"]
    db = SessionLocal()
    try:
        repo = RunRepository(db)
        repo.finish(repo.get(run_id), RunStatus.COMPLETED, answer="x")
    finally:
        db.close()

    with client.stream("GET", f"/api/v1/runs/{run_id}/events") as r:
        # Without these a proxy holds every event until the stream closes —
        # the exact opposite of what SSE is for.
        assert r.headers["cache-control"] == "no-cache"
        assert r.headers["x-accel-buffering"] == "no"
        "".join(r.iter_text())
