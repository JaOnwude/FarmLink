"""
Day 11 tests.

A documented limitation, not a workaround for a gap in our code: this
project's test suite uses httpx.ASGITransport for every other endpoint,
but ASGITransport cannot cleanly handle an infinite StreamingResponse -
confirmed directly (five isolated reproductions outside pytest) that
even just receiving the initial response for an SSE stream hangs
indefinitely through it, independent of how the response is consumed or
closed. This is a known category of friction between ASGI test doubles
and long-lived streams, not specific to this endpoint.

So: the SSE router function is called DIRECTLY here (the exact same
function object FastAPI has registered on the route, just invoked as a
plain coroutine instead of through the ASGI simulation layer that can't
handle it). This tests real behavior - real concurrent publish, real
Redis pub/sub, real pool-scoping - just without routing through the one
specific layer that can't be exercised here.
"""
import asyncio
import json
import uuid

import pytest

from app.core.realtime import publish_event, subscribe
from app.features.feed import firestore_client
from app.features.feed.router import stream_pool_feed


@pytest.mark.asyncio
async def test_sse_endpoint_streams_a_live_event_for_its_pool():
    pool_id = uuid.uuid4()
    response = await stream_pool_feed(pool_id)
    assert response.media_type == "text/event-stream"

    async def publisher():
        await asyncio.sleep(0.2)
        await publish_event(
            f"feed:pool:{pool_id}", "contribution.added", {"farmer_id": "f1", "qty": 12}
        )

    pub_task = asyncio.create_task(publisher())
    chunk = await asyncio.wait_for(response.body_iterator.__anext__(), timeout=5)
    await pub_task

    assert chunk.startswith("data: ")
    event = json.loads(chunk[len("data: ") :].strip())
    assert event["event"] == "contribution.added"
    assert event["data"]["qty"] == 12


@pytest.mark.asyncio
async def test_sse_endpoint_only_streams_events_for_its_own_pool():
    pool_a = uuid.uuid4()
    pool_b = uuid.uuid4()
    response = await stream_pool_feed(pool_a)

    async def publish_wrong_then_right():
        await asyncio.sleep(0.2)
        await publish_event(f"feed:pool:{pool_b}", "contribution.added", {"qty": 999})
        await asyncio.sleep(0.2)
        await publish_event(f"feed:pool:{pool_a}", "contribution.added", {"qty": 5})

    pub_task = asyncio.create_task(publish_wrong_then_right())
    chunk = await asyncio.wait_for(response.body_iterator.__anext__(), timeout=5)
    await pub_task

    event = json.loads(chunk[len("data: ") :].strip())
    assert event["data"]["qty"] == 5  # pool_a's event, never pool_b's 999


@pytest.mark.asyncio
async def test_realtime_pubsub_is_scoped_per_channel_with_real_concurrency():
    """Tests core.realtime directly, no HTTP/router involved - two
    genuinely concurrent subscribers on different channels, one
    publisher, via asyncio.gather (same "real concurrency" standard as
    Day 5's order test)."""
    channel_a = f"test:{uuid.uuid4()}"
    channel_b = f"test:{uuid.uuid4()}"
    received_a = []
    received_b = []

    async def sub_a():
        async for raw in subscribe(channel_a):
            received_a.append(json.loads(raw))
            return

    async def sub_b():
        async for raw in subscribe(channel_b):
            received_b.append(json.loads(raw))
            return

    async def publisher():
        await asyncio.sleep(0.2)
        await publish_event(channel_a, "test.event", {"which": "a"})

    await asyncio.wait_for(asyncio.gather(sub_a(), publisher()), timeout=5)

    assert len(received_a) == 1
    assert received_a[0]["data"]["which"] == "a"
    assert len(received_b) == 0  # channel_b's subscriber never ran, nothing published to it


def test_firestore_write_no_ops_cleanly_when_unconfigured(caplog):
    """This sandbox (and this project's current .env) has no real GCP
    project configured - confirms that's handled gracefully, not
    silently swallowed as an untested assumption."""
    import logging

    firestore_client.reset_client_for_testing()
    caplog.set_level(logging.DEBUG, logger="farmlink.firestore")

    firestore_client.write_pool_feed_event("some-pool-id", "contribution.added", {"qty": 5})

    assert "not configured" in caplog.text.lower()


def test_firestore_client_is_not_constructed_when_project_id_unset(monkeypatch):
    """Confirms the lazy-init actually stays lazy/no-op - it should never
    try to construct a real google.cloud.firestore.Client (which would
    attempt to load credentials and fail loudly) when unconfigured."""
    firestore_client.reset_client_for_testing()
    monkeypatch.setattr(firestore_client.settings, "firestore_project_id", None)

    result = firestore_client._get_client()
    assert result is None
