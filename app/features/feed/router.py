"""
Feed feature - HTTP layer only. One endpoint: the SSE stream.
"""
import uuid

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.realtime import subscribe

router = APIRouter(prefix="/feed", tags=["feed"])


@router.get("/pools/{pool_id}/stream")
async def stream_pool_feed(pool_id: uuid.UUID) -> StreamingResponse:
    """Server-Sent Events: live activity on one pool (contributions,
    orders, closes) as they happen. No auth dependency - same reasoning
    as GET /pools being public: watching a pool fill up in real time is
    exactly the kind of thing that should work before someone has an
    account, same as browsing the pool listing itself.

    Cleanup when a client disconnects (closed tab, lost connection) is
    handled by core.realtime.subscribe's own finally block, triggered
    when this generator is closed/garbage collected - not by explicitly
    polling Request.is_disconnected(), which doesn't behave reliably
    under every ASGI transport (notably: this project's own test
    client).
    """

    async def event_stream():
        async for raw_json in subscribe(f"feed:pool:{pool_id}"):
            yield f"data: {raw_json}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Nginx-specific, harmless elsewhere: prevents a reverse
            # proxy from buffering the stream and defeating "real-time".
            "X-Accel-Buffering": "no",
        },
    )
