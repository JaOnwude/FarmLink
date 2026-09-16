"""
Day 1 smoke test. Not one of the five concurrency tests (those land Day 5
against a real Postgres via testcontainers) — this just proves the app
boots, middleware doesn't crash the request, and settings load correctly.
"""
from fastapi.testclient import TestClient

from app.main import app


def test_health_check():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_request_id_is_echoed_back():
    client = TestClient(app)
    response = client.get("/health", headers={"X-Request-ID": "test-123"})
    assert response.headers["X-Request-ID"] == "test-123"


def test_request_id_is_generated_when_absent():
    client = TestClient(app)
    response = client.get("/health")
    assert "X-Request-ID" in response.headers
    assert len(response.headers["X-Request-ID"]) > 0
