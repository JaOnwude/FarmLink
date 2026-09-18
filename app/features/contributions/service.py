"""
Contributions feature - business logic, called by router.py.

A farmer contributing stock to a CLOSED pool must be rejected with 409 -
this is one of the five must-pass tests from the brief ("Closed pool
rejects orders and contributions with 409"). The lock pattern here is
the same one Day 5's order placement uses on the same table: lock the
pool row first, THEN check its status, so a contribution can't sneak in
between an admin's close_pool check and its commit.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.contributions.models import Contribution
from app.features.pools.models import Pool, PoolStatus


class PoolNotFound(Exception):
    pass


class PoolClosed(Exception):
    pass


async def create_contribution(
    db: AsyncSession, farmer_id: uuid.UUID, pool_id: uuid.UUID, qty: int
) -> Contribution:
    result = await db.execute(select(Pool).where(Pool.id == pool_id).with_for_update())
    pool = result.scalar_one_or_none()
    if pool is None:
        await db.rollback()
        raise PoolNotFound(pool_id)

    if pool.status != PoolStatus.OPEN:
        await db.rollback()
        raise PoolClosed(pool_id)

    pool.total_qty += qty
    pool.available_qty += qty

    contribution = Contribution(pool_id=pool_id, farmer_id=farmer_id, qty=qty)
    db.add(contribution)

    await db.commit()
    await db.refresh(contribution)
    return contribution


async def list_contributions_for_pool(db: AsyncSession, pool_id: uuid.UUID) -> list[Contribution]:
    result = await db.execute(
        select(Contribution)
        .where(Contribution.pool_id == pool_id)
        .order_by(Contribution.created_at.desc())
    )
    return list(result.scalars().all())
