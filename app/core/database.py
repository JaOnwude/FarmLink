"""
Async SQLAlchemy engine and session factory.

Why async engine + asyncpg: the whole point of FastAPI here is to not block
the event loop while Postgres holds a row lock during the pooled-stock
transaction (SELECT ... FOR UPDATE). A sync driver would tie up a worker
thread per in-flight locked transaction; asyncpg lets many waiting buyers
sit on the same event loop cheaply.
"""
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()

if settings.is_production:
    # Real connection pooling in production - a handful of long-lived
    # connections reused across requests, which is what pool_size/
    # max_overflow are for.
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,  # drop dead connections instead of erroring
        pool_size=10,
        max_overflow=20,
    )
else:
    # NullPool everywhere else (dev, CI, tests): every checkout opens a
    # fresh asyncpg connection and closes it when done - nothing persists
    # between calls. This isn't a performance choice, it's a correctness
    # one: pytest-asyncio gives each test function its own event loop, and
    # a real connection pool holds onto connections tied to whichever loop
    # created them - reusing one from a closed loop crashes with
    # "attached to a different loop" / "Event loop is closed". NullPool
    # sidesteps the whole problem by never reusing a connection across
    # loop boundaries.
    engine = create_async_engine(settings.database_url, poolclass=NullPool)

AsyncSessionLocal = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


class Base(DeclarativeBase):
    pass


class UUIDPKMixin:
    """Every feature's tables use this - a UUID primary key rather than a
    sequential integer. Deliberate for a co-op marketplace: order/pool IDs
    end up in URLs, receipts, and Paystack references, and a sequential ID
    would let anyone guess how many orders/pools exist by counting up.
    Generated in Python (uuid4) rather than relying on a Postgres extension,
    so it works identically on any Postgres host with zero setup."""

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    """created_at with a Python-side UTC default. Used wherever the schema
    calls for an 'at' / 'created_at' column."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one session per request, always closed."""
    async with AsyncSessionLocal() as session:
        yield session

