"""
Implements "Middleware: request-id, timing, logging" from the flowchart.

Every request gets a unique X-Request-ID (accepted from the client if
supplied, generated otherwise) which is:
  - attached to every log line for that request
  - echoed back on the response, matching "Response model -> status code
    + X-Request-ID" at the end of the flowchart
  - useful later to correlate a Paystack webhook failure with the order
    request that triggered it
"""
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("farmlink.request")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        start = time.perf_counter()

        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            status_code = response.status_code if response else 500
            logger.info(
                f"{request.method} {request.url.path} -> {status_code} ({duration_ms}ms)",
                extra={"request_id": request_id},
            )
            if response is not None:
                response.headers["X-Request-ID"] = request_id
