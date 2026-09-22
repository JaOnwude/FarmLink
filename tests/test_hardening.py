"""
Day 13 tests: hardening.
"""
import json
import logging
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.database import AsyncSessionLocal
from app.core.logging_config import JSONFormatter
from app.core.security import create_access_token, hash_password
from app.features.auth.models import User, UserRole
from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _make_user(role: UserRole, approved: bool = True) -> User:
    async with AsyncSessionLocal() as db:
        user = User(
            email=f"{role.value}-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("whatever-12345"),
            role=role,
            approved=approved,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


def _auth_header(user: User) -> dict:
    token = create_access_token(user.id, user.role)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_unhandled_exception_returns_clean_500_not_a_traceback(client, monkeypatch, caplog):
    """The core hardening fix: an unexpected bug must never leak internals
    to the client, but MUST be fully logged (with traceback + request_id)
    on our side."""
    caplog.set_level(logging.ERROR, logger="farmlink.errors")

    async def boom(*args, **kwargs):
        raise RuntimeError("simulated unexpected failure - database on fire")

    monkeypatch.setattr("app.features.pools.service.list_pools", boom)

    resp = await client.get("/api/v1/pools", headers={"X-Request-ID": "test-trace-123"})

    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == "Internal server error"
    assert "database on fire" not in json.dumps(body)  # never leaked to the client
    assert body["request_id"] == "test-trace-123"
    assert resp.headers["X-Request-ID"] == "test-trace-123"

    # But it WAS fully captured server-side, correlated by the same id.
    # caplog.text uses pytest's default formatter, which doesn't render
    # custom extra={} fields as text even though they're really on the
    # record - check the record directly instead of string-searching
    # rendered text.
    assert "database on fire" in caplog.text
    assert any(getattr(r, "request_id", None) == "test-trace-123" for r in caplog.records)


def test_wildcard_cors_is_rejected_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("PAYSTACK_PUBLIC_KEY", "pk_test_x")

    with pytest.raises(ValueError, match="non-wildcard"):
        Settings(_env_file=None)


def test_specific_cors_origin_is_accepted_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://farmlink.example.com")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_live_x")
    monkeypatch.setenv("PAYSTACK_PUBLIC_KEY", "pk_live_x")

    settings = Settings(_env_file=None)  # must not raise
    assert settings.cors_origins_list == ["https://farmlink.example.com"]


def test_json_formatter_produces_valid_structured_logs():
    """Never actually verified since Day 1 - configure_logging() is
    called in the app's lifespan, which this suite's ASGITransport never
    triggers (confirmed back on Day 9), so nothing exercised the real
    JSON output shape until now."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="farmlink.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="a test log line",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-abc-123"

    output = formatter.format(record)
    parsed = json.loads(output)  # must be valid JSON, not just formatted text

    assert parsed["level"] == "INFO"
    assert parsed["message"] == "a test log line"
    assert parsed["request_id"] == "req-abc-123"
    assert parsed["logger"] == "farmlink.test"
    assert "timestamp" in parsed


def test_json_formatter_includes_exception_traceback_when_present():
    formatter = JSONFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="farmlink.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="something broke",
            args=(),
            exc_info=sys.exc_info(),
        )
    output = formatter.format(record)
    parsed = json.loads(output)
    assert "exception" in parsed
    assert "ValueError: boom" in parsed["exception"]


@pytest.mark.asyncio
async def test_twenty_concurrent_buyers_race_five_units_no_oversell(client: AsyncClient):
    """A bigger version of Day 5's concurrency test - 20 genuinely
    concurrent buyers (not 2), racing for only 5 available units. Proves
    the lock holds at higher contention, not just the minimal case."""
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)

    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Rice", "price": "1000.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]
    await client.post(
        "/api/v1/contributions", json={"pool_id": pool_id, "qty": 5}, headers=_auth_header(farmer)
    )

    buyers = [await _make_user(UserRole.BUYER) for _ in range(20)]

    import asyncio

    async def place_order(buyer):
        return await client.post(
            "/api/v1/orders", json={"pool_id": pool_id, "qty": 1}, headers=_auth_header(buyer)
        )

    responses = await asyncio.gather(*(place_order(b) for b in buyers))
    statuses = [r.status_code for r in responses]

    assert statuses.count(201) == 5   # exactly the available stock, no more
    assert statuses.count(409) == 15  # everyone else correctly rejected

    pool_after = await client.get(f"/api/v1/pools/{pool_id}")
    assert pool_after.json()["available_qty"] == 0  # never negative, never left over
