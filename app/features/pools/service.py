"""
Pools feature - business logic, called by router.py.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.pools.models import Pool, PoolStatus
from app.features.pools.schemas import PoolCreate


class PoolNotFound(Exception):
    pass


class PoolAlreadyClosed(Exception):
    pass


async def create_pool(db: AsyncSession, data: PoolCreate) -> Pool:
    pool = Pool(
        product=data.product,
        price=data.price,
        total_qty=0,
        available_qty=0,
        status=PoolStatus.OPEN,
    )
    db.add(pool)
    await db.commit()
    await db.refresh(pool)
    return pool


async def list_pools(db: AsyncSession, status_filter: PoolStatus | None = None) -> list[Pool]:
    stmt = select(Pool).order_by(Pool.created_at.desc())
    if status_filter is not None:
        stmt = stmt.where(Pool.status == status_filter)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_pool(db: AsyncSession, pool_id: uuid.UUID) -> Pool:
    result = await db.execute(select(Pool).where(Pool.id == pool_id))
    pool = result.scalar_one_or_none()
    if pool is None:
        raise PoolNotFound(pool_id)
    return pool


async def close_pool(db: AsyncSession, pool_id: uuid.UUID) -> Pool:
    """Admin manually closes a pool once they judge enough has been
    contributed / sold. Locked the same way the order flow locks a pool -
    closing while an order is mid-flight on the same row waits for that
    transaction rather than racing it."""
    result = await db.execute(select(Pool).where(Pool.id == pool_id).with_for_update())
    pool = result.scalar_one_or_none()
    if pool is None:
        raise PoolNotFound(pool_id)
    if pool.status == PoolStatus.CLOSED:
        raise PoolAlreadyClosed(pool_id)
    pool.status = PoolStatus.CLOSED
    await db.commit()
    await db.refresh(pool)
    return pool
