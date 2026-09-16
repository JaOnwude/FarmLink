"""
Async SQLAlchemy engine and session factory.

Why async engine + asyncpg: the whole point of FastAPI here is to not block
the event loop while Postgres holds a row lock during the pooled-stock
transaction (SELECT ... FOR UPDATE). A sync driver would tie up a worker
thread per in-flight locked transaction; asyncpg lets many waiting buyers
sit on the same event loop cheaply.
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,      # drop dead connections instead of erroring
    pool_size=10,
    max_overflow=20,
    echo=settings.environment == "development",
)

AsyncSessionLocal = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one session per request, always closed."""
    async with AsyncSessionLocal() as session:
        yield session
