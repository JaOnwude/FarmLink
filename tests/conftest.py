"""
Applies to every test in the suite (root conftest.py, not scoped to one
feature) because the rate-limit middleware runs on every request through
`app`, so every test that hits the app touches the shared Redis client.

Why this is needed: pytest-asyncio gives each test function its own event
loop, but app.core.redis_client.redis_client is a single module-level
instance created once at import time and reused for the whole test
session. redis-py's connection pool holds onto a connection tied to
whichever loop was running when it was opened - reuse it from a new
test's loop and it fails the same way a real Postgres connection pool
did (see app/core/database.py's NullPool comment for the same root
cause). Postgres solved this by never pooling outside production;
disconnecting Redis after each test is the equivalent for a client
that's cheap to reconnect.
Also resets the rate-limit buckets before every test. Without this, tests
that hit /auth/login or /auth/register (now rate-limited at just 5 per 5
minutes, see Day 6's tuning of the auth-specific bucket) would start
failing with 429s a few tests into the session, purely because they all
share the same simulated client IP (httpx's ASGITransport defaults to
127.0.0.1) - not because anything is actually broken.
"""
import pytest_asyncio

from app.core.redis_client import redis_client


@pytest_asyncio.fixture(autouse=True)
async def _reset_rate_limit_and_redis_connection():
    await redis_client.delete("ratelimit:127.0.0.1", "ratelimit:auth:127.0.0.1")
    yield
    await redis_client.aclose()
