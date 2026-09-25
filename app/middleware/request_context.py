"""
Request-scoped logging middleware: assigns a request ID, times the
request, logs a one-line summary, and turns any unhandled exception into
a clean 500 instead of a raw traceback reaching the client.

Every request gets a unique X-Request-ID (accepted from the client if
supplied, generated otherwise) which is:
  - attached to every log line for that request, so all log lines from
    one request can be grepped/filtered together
  - echoed back on the response headers, so the client can quote it when
    reporting a problem
  - useful later to correlate a Paystack webhook failure with the order
    request that triggered it

Also the actual home of this app's unhandled-exception handling - NOT
FastAPI's @app.exception_handler(Exception), which doesn't reliably
catch exceptions when custom BaseHTTPMiddleware subclasses (this one,
and RateLimitMiddleware) are in the stack. Confirmed directly: a
registered Exception handler let a raised RuntimeError propagate all
the way to a bare 500 with none of its own logic running. This is a
known Starlette/FastAPI interaction, not a configuration mistake - the
outermost middleware that ACTUALLY sees the exception is the reliable
place to handle it.
"""
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("farmlink.request")
error_logger = logging.getLogger("farmlink.errors")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        start = time.perf_counter()

        response = None
        try:
            response = await call_next(request)
            return response
        except Exception as exc:
            # Never leak the exception message/traceback to the client -
            # only a generic message and the request_id, which is safe
            # to hand back (useful for the client to quote when
            # reporting a problem) and correlates directly with the full
            # server-side log line below.
            error_logger.error(
                f"Unhandled exception on {request.method} {request.url.path}",
                exc_info=exc,
                extra={"request_id": request_id},
            )
            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal server error", "request_id": request_id},
            )
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
