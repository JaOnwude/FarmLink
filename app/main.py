"""
Application entrypoint. Builds the FastAPI app, wires up middleware in the
correct order, starts/stops the background scheduler, and mounts every
feature's router under a common API prefix.
"""

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
from app.features.feed.router import router as feed_router
from app.features.payouts.router import router as payouts_router
from app.features.auth.service import bootstrap_admin

settings = get_settings()
# A single scheduler instance, shared for the lifetime of the process. It
# only actually runs jobs once `lifespan` calls scheduler.start() below.
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once when the app starts, and again (the code after `yield`) when
    it shuts down. This is where anything "global" to the process - logging,
    the background job scheduler, closing shared connections - belongs,
    because it only needs to happen once per process, not once per request.
    """
    configure_logging()

    if settings.admin_bootstrap_email and settings.admin_bootstrap_password:
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            await bootstrap_admin(db, settings.admin_bootstrap_email, settings.admin_bootstrap_password)

    # The order sweep is a background job, not something triggered by an
    # incoming request: it periodically finds orders that were created but
    # never paid for, and releases whatever they were holding (e.g. pool
    # inventory) back for others to claim. It runs on its own timer here
    # rather than being kicked off by any single request.
    #
    # `disable_scheduler` exists purely for multi-instance deployments: if
    # you run more than one copy of this API in production, each instance
    # would otherwise start its own independent timer, and all of them
    # would try to sweep the same rows at once. In that setup you disable
    # the scheduler on every instance except one. It is NOT needed to
    # silence the scheduler during automated tests - the test client used
    # in this project never triggers FastAPI's lifespan at all, so the
    # scheduler simply never starts when running the test suite.
    if not settings.disable_scheduler:
        scheduler.add_job(
            sweep_expired_orders,
            "interval",
            minutes=settings.sweep_interval_minutes,
            id="sweep_expired_orders",
        )
        scheduler.start()

    yield  # the app runs here; everything below is shutdown/cleanup

    if scheduler.running:
        scheduler.shutdown(wait=False)
    await redis_client.aclose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

# Middleware order matters: FastAPI/Starlette runs middleware in the order
# it's added, but the OUTERMOST one added last actually wraps around the
# others - so RateLimitMiddleware (added last, below) is the very first
# thing a request hits. That's intentional: a client that's rate-limited
# gets rejected before request-id assignment or logging even runs, so a
# flood of blocked requests doesn't also flood the application logs.
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
    """Liveness/readiness probe — also the first thing to smoke-test once
    docker-compose is up, since it needs no auth and no dependencies."""
    return {"status": "ok", "environment": settings.environment}


# Unhandled exceptions are caught inside RequestContextMiddleware rather
# than with a standard @app.exception_handler decorator here. That's a
# deliberate workaround, not a style choice: FastAPI's exception handlers
# don't reliably fire when the error is raised inside another
# BaseHTTPMiddleware (like the two above), so catching it at the
# middleware level is what actually works. See RequestContextMiddleware's
# own docstring for the full explanation.


# Each feature (auth, pools, orders, etc.) owns its own APIRouter and is
# mounted here under one shared "/api/v1"-style prefix, so every route in
# the app is versioned consistently without each feature having to know
# the prefix itself.
app.include_router(auth_router, prefix=settings.api_v1_prefix)
app.include_router(pools_router, prefix=settings.api_v1_prefix)
app.include_router(contributions_router, prefix=settings.api_v1_prefix)
app.include_router(orders_router, prefix=settings.api_v1_prefix)
app.include_router(payments_router, prefix=settings.api_v1_prefix)
app.include_router(feed_router, prefix=settings.api_v1_prefix)
app.include_router(payouts_router, prefix=settings.api_v1_prefix)
