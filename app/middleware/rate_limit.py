"""
Implements "Within rate limit? -> No -> 429 + Retry-After" from the
flowchart - the very first check a request hits, before auth or anything
else runs.

Real token bucket, not a fixed-window counter: capacity refills
continuously at a fixed rate rather than resetting in a lump at window
boundaries, so a caller can't burst right at a window edge (send max
requests at 0:59, then max again at 1:00). State (tokens remaining, last
refill time) lives in Redis so it's shared correctly across however many
API worker processes are running - a single process's in-memory counter
would let a caller get a fresh limit just by hitting a different worker.

The refill math + the check-and-decrement have to happen as one atomic
operation, or two requests arriving at the same instant could both read
"1 token left" and both proceed. Redis doesn't make a plain multi-step
read-then-write atomic on its own, so this runs as a Lua script - Redis
executes the whole script as a single operation, nothing else can
interleave.
"""
import math
import time

from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import get_settings

settings = get_settings()

_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])  -- tokens per second
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local bucket = redis.call("HMGET", key, "tokens", "timestamp")
local tokens = tonumber(bucket[1])
local timestamp = tonumber(bucket[2])

if tokens == nil then
  tokens = capacity
  timestamp = now
end

local elapsed = math.max(0, now - timestamp)
tokens = math.min(capacity, tokens + (elapsed * refill_rate))

local allowed = 0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
end

redis.call("HMSET", key, "tokens", tokens, "timestamp", now)
redis.call("EXPIRE", key, math.ceil(capacity / refill_rate) + 1)

return {allowed, tostring(tokens)}
"""


class RateLimitMiddleware(BaseHTTPMiddleware):
    # Path suffixes that get the stricter, auth-specific bucket instead of
    # the general one. Checked with .endswith() so this doesn't need to
    # know about the versioned /api/v1 prefix.
    _SENSITIVE_PATHS = {"/auth/login", "/auth/register"}

    def __init__(self, app, redis_client: Redis):
        super().__init__(app)
        self.redis = redis_client
        self.capacity = settings.rate_limit_requests
        self.refill_rate = settings.rate_limit_requests / settings.rate_limit_window_seconds
        self.auth_capacity = settings.auth_rate_limit_requests
        self.auth_refill_rate = (
            settings.auth_rate_limit_requests / settings.auth_rate_limit_window_seconds
        )
        self._script = self.redis.register_script(_TOKEN_BUCKET_LUA)

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            # Don't let container/load-balancer health checks (which poll
            # frequently and aren't a real client) burn a real client's
            # rate budget or get rate-limited themselves.
            return await call_next(request)

        # Keyed by client IP - once auth exists on a route (Day 3+), a
        # per-user key would be more precise, but IP is the only thing
        # available before authentication even runs, and this check has
        # to happen first per the flowchart.
        client_ip = request.client.host if request.client else "unknown"

        is_sensitive = any(request.url.path.endswith(p) for p in self._SENSITIVE_PATHS)
        if is_sensitive:
            # A separate bucket (separate Redis key, separate capacity)
            # from the general one - a login attempt shouldn't spend from
            # the same budget as ordinary browsing, and vice versa: five
            # failed logins shouldn't lock a buyer out of viewing pools.
            key = f"ratelimit:auth:{client_ip}"
            capacity, refill_rate = self.auth_capacity, self.auth_refill_rate
        else:
            key = f"ratelimit:{client_ip}"
            capacity, refill_rate = self.capacity, self.refill_rate

        allowed, tokens_remaining = await self._script(
            keys=[key],
            args=[capacity, refill_rate, time.time(), 1],
        )

        if not allowed:
            tokens_remaining = float(tokens_remaining)
            retry_after = math.ceil((1 - tokens_remaining) / refill_rate)
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(max(retry_after, 1))},
            )

        return await call_next(request)
