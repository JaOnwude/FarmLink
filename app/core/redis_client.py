"""
One shared async Redis client for the whole app. Created once at import
time (connection pooling is Redis's own client's job, unlike the Postgres
NullPool situation - redis-py's async client is safe to share across
requests and doesn't have the same per-event-loop connection binding
problem asyncpg has).
"""
import redis.asyncio as redis

from app.core.config import get_settings

settings = get_settings()

redis_client: redis.Redis = redis.from_url(settings.redis_url, decode_responses=True)


async def get_redis() -> redis.Redis:
    return redis_client
