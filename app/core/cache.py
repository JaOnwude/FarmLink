"""
Generic cache-aside helpers over the shared Redis client. Implements the
flowchart's "Service layer (business rules) -> Cache hit (Redis)? -> Yes
-> Return cached response / No -> ... -> Invalidate cache keys touched by
the write" steps.

Deliberately generic (JSON in, JSON out) rather than pool-specific - it
lives in core/ because caching is a pattern every feature can reach for,
not a pools-only concern. Each feature decides its own key naming and
what to invalidate when; this module just does the get/set/delete.
"""
import json

from app.core.redis_client import redis_client

CACHE_PREFIX = "cache:"


async def cache_get(key: str) -> dict | list | None:
    raw = await redis_client.get(f"{CACHE_PREFIX}{key}")
    if raw is None:
        return None
    return json.loads(raw)


async def cache_set(key: str, value: dict | list, ttl_seconds: int) -> None:
    await redis_client.set(f"{CACHE_PREFIX}{key}", json.dumps(value), ex=ttl_seconds)


async def cache_delete(*keys: str) -> None:
    if not keys:
        return
    await redis_client.delete(*[f"{CACHE_PREFIX}{k}" for k in keys])
