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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.auth.models import User
from app.features.contributions.models import Contribution
from app.features.feed.service import publish_pool_event
from app.features.pools.models import Pool, PoolStatus
from app.features.pools.service import invalidate_pool_cache


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
    # A contribution changes total_qty/available_qty on the pool - any
    # cached listing or single-pool view is stale as of this commit.
    await invalidate_pool_cache(pool_id)
    await publish_pool_event(
        pool_id,
        "contribution.added",
        {"farmer_id": str(farmer_id), "qty": qty, "available_qty": pool.available_qty},
    )
    return contribution


async def list_contributions_for_pool(db: AsyncSession, pool_id: uuid.UUID) -> list[Contribution]:
    result = await db.execute(
        select(Contribution)
        .where(Contribution.pool_id == pool_id)
        .order_by(Contribution.created_at.desc())
    )
    return list(result.scalars().all())


async def get_pool_contributor_summary(
    db: AsyncSession, pool_id: uuid.UUID
) -> list[tuple[uuid.UUID, str, int]]:
    """One row per farmer with their TOTAL contributed quantity for this
    pool - a GROUP BY done in the database, not raw rows summed in
    Python. Answers "who contributed how much" directly, rather than
    making the caller fetch every individual contribution and add it up
    themselves."""
    result = await db.execute(
        select(Contribution.farmer_id, User.email, func.sum(Contribution.qty))
        .join(User, User.id == Contribution.farmer_id)
        .where(Contribution.pool_id == pool_id)
        .group_by(Contribution.farmer_id, User.email)
        .order_by(func.sum(Contribution.qty).desc())
    )
    return list(result.all())
