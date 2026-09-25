"""
Feed feature - business logic. One function, called from contributions,
orders, and pool close: publish_pool_event.

Two writes, deliberately different urgency:
  1. Redis pub/sub (core.realtime) - awaited inline, right in the request
     path. This is what a connected SSE client actually sees, and it
     needs to be fast and immediate or "real-time" is a lie.
  2. Firestore (firestore_client) - enqueued via the background job queue, not
     awaited inline. This is the durable record, and it can tolerate a
     few seconds of delay - it must NEVER add latency to placing an
     order or a contribution, and a slow/unreachable Firestore must
     never fail the request that triggered it.
"""
import uuid

from app.core.jobs import enqueue
from app.core.realtime import publish_event
from app.features.feed.firestore_client import write_pool_feed_event


def _channel(pool_id: uuid.UUID) -> str:
    return f"feed:pool:{pool_id}"


async def publish_pool_event(pool_id: uuid.UUID, event_type: str, data: dict) -> None:
    await publish_event(_channel(pool_id), event_type, data)
    enqueue(write_pool_feed_event, str(pool_id), event_type, data)
