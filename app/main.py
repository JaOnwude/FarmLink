from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

import app.core.model_registry  # noqa: F401 - registers all ORM models, must run before any DB access
from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.core.redis_client import redis_client
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_context import RequestContextMiddleware
from app.features.auth.router import router as auth_router
from app.features.pools.router import router as pools_router
from app.features.contributions.router import router as contributions_router
from app.features.orders.router import router as orders_router
from app.features.orders.service import sweep_expired_orders
from app.features.payments.router import router as payments_router

settings = get_settings()
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    # The sweep never runs on request traffic - it's a background job on
    # its own clock, exactly as the brief specifies ("A scheduled sweep
    # releases unpaid orders... also under the lock"). disable_scheduler
    # exists for deployment, not tests: this suite's httpx ASGITransport
    # never triggers FastAPI's lifespan at all (verified directly - a
    # request through it leaves scheduler.running False), so the real
    # scheduler never starts during any test regardless of this flag.
    # It matters once there's more than one API instance running in
    # production - each replica would otherwise start its own sweep timer
    # and all of them would race the same rows.
    if not settings.disable_scheduler:
        scheduler.add_job(
            sweep_expired_orders,
            "interval",
            minutes=settings.sweep_interval_minutes,
            id="sweep_expired_orders",
        )
        scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)
    await redis_client.aclose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

# Order matters: outermost middleware runs first on the way in. Rate limit
# is outermost of the three - it's the very first check in the flowchart,
# before request-id/logging even runs, so a flood of rejected requests
# doesn't also flood the logs.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(RateLimitMiddleware, redis_client=redis_client)


@app.get("/health")
async def health():
    """Liveness/readiness probe — also useful as the first thing to
    smoke-test once docker-compose is up."""
    return {"status": "ok", "environment": settings.environment}


# Each feature owns its own router; wire them in here as they're built.
app.include_router(auth_router, prefix=settings.api_v1_prefix)
app.include_router(pools_router, prefix=settings.api_v1_prefix)
app.include_router(contributions_router, prefix=settings.api_v1_prefix)
app.include_router(orders_router, prefix=settings.api_v1_prefix)
app.include_router(payments_router, prefix=settings.api_v1_prefix)
# from app.features.payouts.router import router as payouts_router
# from app.features.feed.router import router as feed_router
#
# app.include_router(pools_router, prefix=settings.api_v1_prefix)
# ...
