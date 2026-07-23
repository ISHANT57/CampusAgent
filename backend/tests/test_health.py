"""M5's Definition of Done, as a runnable check.

The claim is that /health survives a dead database while /health/deps reports
it. An untested claim about failure behaviour is a hope.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_is_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_survives_a_dead_database():
    # THE point of M5. If this ever fails, a Neon blip becomes a restart loop.
    with patch("app.api.v1.health.engine.connect", side_effect=OSError("connection refused")):
        assert client.get("/health").status_code == 200


def test_deps_reports_a_dead_database_without_raising():
    with patch("app.api.v1.health.engine.connect", side_effect=OSError("connection refused")):
        r = client.get("/health/deps")
    assert r.status_code == 200          # the report is the payload, not the status
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"]["status"] == "error"


def test_deps_does_not_leak_connection_details():
    # Unauthenticated endpoint. Driver errors routinely echo the DSN back, so
    # only the exception type is reported.
    secret = "postgresql://user:hunter2@host/db"
    with patch("app.api.v1.health.engine.connect", side_effect=OSError(secret)):
        body = client.get("/health/deps").json()
    assert "hunter2" not in str(body)
    assert body["checks"]["database"]["error"] == "OSError"


def test_deps_ok_against_the_real_database():
    body = client.get("/health/deps").json()
    assert body["checks"]["database"]["status"] == "ok"
