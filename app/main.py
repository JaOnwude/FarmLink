from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

import app.core.model_registry  # noqa: F401 - registers all ORM models, must run before any DB access
from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.core.redis_client import redis_client
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_context import RequestContextMiddleware
from app.features.auth.router import router as auth_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    # Day 4+ will add: DB connectivity check on startup, Firestore init
    yield
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
# from app.features.pools.router import router as pools_router
# from app.features.contributions.router import router as contributions_router
# from app.features.orders.router import router as orders_router
# from app.features.payments.router import router as payments_router
# from app.features.payouts.router import router as payouts_router
# from app.features.feed.router import router as feed_router
#
# app.include_router(pools_router, prefix=settings.api_v1_prefix)
# ...
