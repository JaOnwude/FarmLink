"""
Redis pub/sub as the real-time transport for the SSE feed. Cross-cutting
(lives in core/, not features/feed/) since any feature can publish an
event - contributions, orders, and pool closes all do.

Why Redis pub/sub here rather than Firestore directly: Redis is already
a hard dependency (cache, rate limiting, job queue), PUBLISH is a
fire-and-forget operation with no persistence overhead, and it's
actually testable in this environment. Firestore (features/feed/
firestore_client.py) is the durable copy of the same events - pub/sub is
the live transport, Firestore is the record. A subscriber that's offline
when an event fires simply misses it on the live stream, same as any
pub/sub system; anyone needing history reads Firestore instead.
"""
import json
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from app.core.redis_client import redis_client


async def publish_event(channel: str, event_type: str, data: dict) -> None:
    payload = {
        "event": event_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await redis_client.publish(channel, json.dumps(payload))


async def subscribe(channel: str) -> AsyncGenerator[str, None]:
    """Yields raw JSON strings as they're published to `channel`. Caller
    is responsible for turning each one into an SSE-formatted chunk -
    this function only knows about Redis, not HTTP."""
    pubsub = redis_client.pubsub()
    try:
        await pubsub.subscribe(channel)
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue  # "subscribe" confirmation messages, not real events
            yield message["data"]
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
