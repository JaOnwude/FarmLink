from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.middleware.request_context import RequestContextMiddleware

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    # Day 2+ will add: DB connectivity check, Redis ping, Firestore init
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

# Order matters: outermost middleware runs first on the way in.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestContextMiddleware)


@app.get("/health")
async def health():
    """Liveness/readiness probe — also useful as the first thing to
    smoke-test once docker-compose is up."""
    return {"status": "ok", "environment": settings.environment}


# Each feature owns its own router; wire them in here as they're built.
# from app.features.auth.router import router as auth_router
# from app.features.pools.router import router as pools_router
# from app.features.contributions.router import router as contributions_router
# from app.features.orders.router import router as orders_router
# from app.features.payments.router import router as payments_router
# from app.features.payouts.router import router as payouts_router
# from app.features.feed.router import router as feed_router
#
# app.include_router(auth_router, prefix=settings.api_v1_prefix)
# app.include_router(pools_router, prefix=settings.api_v1_prefix)
# ...
