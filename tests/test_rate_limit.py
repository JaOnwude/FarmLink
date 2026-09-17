"""
Day 3 test: proves the rate limiter actually limits, not just that it
doesn't crash.

Important hygiene note: this deliberately manipulates the SAME Redis key
("ratelimit:127.0.0.1") every test client shares, because httpx's
ASGITransport defaults the simulated client to that IP for every test in
the suite. An earlier version of this test set the bucket's timestamp far
in the future and never cleaned up - since a token bucket only refills
based on elapsed time, a future timestamp means it never refills, so
every later test sharing that IP got permanently rate-limited too. Always
delete the key in a finally block, not just before the test.
"""
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.redis_client import redis_client

RATE_LIMIT_KEY = "ratelimit:127.0.0.1"


@pytest.mark.asyncio
async def test_rate_limiter_returns_429_with_retry_after():
    from app.main import app

    await redis_client.delete(RATE_LIMIT_KEY)
    try:
        # Force the bucket down to its last token, with a timestamp of
        # "now" so it behaves like a real bucket that just got drained -
        # not one that's artificially frozen forever.
        await redis_client.hset(
            RATE_LIMIT_KEY, mapping={"tokens": "1", "timestamp": str(time.time())}
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            first = await ac.get("/api/v1/auth/me")  # 401, but that's fine - rate limit runs before auth
            assert first.status_code in (401, 200)

            second = await ac.get("/api/v1/auth/me")
            assert second.status_code == 429
            assert "Retry-After" in second.headers
            assert int(second.headers["Retry-After"]) >= 1
    finally:
        await redis_client.delete(RATE_LIMIT_KEY)
