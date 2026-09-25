"""
Payouts feature - business logic.

Core rule: sharing out the revenue from a closed pool can happen only
once. Farmers who contributed stock to the pool each get a share of the
pool's total revenue, in proportion to how much they contributed, and
that calculation must never run twice for the same pool.

Two independent guards against a double payout, not one:
  1. An explicit pre-check (any payout rows already exist for this pool?)
     under the same SELECT ... FOR UPDATE lock every other pool mutation
     in this project uses - the first line of defense, and the one that
     gives a clean, immediate 409 in the normal case.
  2. A UNIQUE(pool_id, farmer_id) constraint on the payouts table itself,
     which catches it even if two "pay out this pool" requests somehow
     both got past guard #1 (they can't, under the lock - but a
     constraint that makes the invariant true at the database level,
     not just in this one code path, costs nothing and is worth having).

Payout amounts are computed to sum to EXACTLY the pool's revenue, not
approximately: splitting a currency amount N ways by proportion rarely
divides evenly, so each farmer's share is rounded DOWN, and whatever's
left over from that rounding (at most a few kobo) goes to the last
farmer. This is the standard fix for exact-sum currency splitting.
"""
import uuid
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.contributions.service import get_pool_contributor_summary
from app.features.orders.models import Order, OrderStatus
from app.features.payouts.models import Payout
from app.features.pools.models import Pool, PoolStatus

_CENTS = Decimal("0.01")


class PoolNotFound(Exception):
    pass


class PoolNotClosed(Exception):
    pass


class PoolAlreadyPaidOut(Exception):
    pass


async def calculate_and_create_payouts(db: AsyncSession, pool_id: uuid.UUID) -> list[Payout]:
    result = await db.execute(select(Pool).where(Pool.id == pool_id).with_for_update())
    pool = result.scalar_one_or_none()
    if pool is None:
        await db.rollback()
        raise PoolNotFound(pool_id)

    # Payout only makes sense once the pool is done taking orders - an
    # open pool's revenue isn't final yet.
    if pool.status != PoolStatus.CLOSED:
        await db.rollback()
        raise PoolNotClosed(pool_id)

    already_paid = await db.execute(select(Payout.id).where(Payout.pool_id == pool_id).limit(1))
    if already_paid.scalar_one_or_none() is not None:
        await db.rollback()
        raise PoolAlreadyPaidOut(pool_id)

    contributor_rows = await get_pool_contributor_summary(db, pool_id)
    if not contributor_rows:
        # Closed with no contributions at all - nothing to pay out, and
        # not an error (an admin might close an empty pool by mistake,
        # or a test pool that never got funded).
        await db.commit()
        return []

    revenue_result = await db.execute(
        select(func.coalesce(func.sum(Order.total), 0)).where(
            Order.pool_id == pool_id, Order.status == OrderStatus.PAID
        )
    )
    total_revenue: Decimal = revenue_result.scalar_one()
    if total_revenue <= 0:
        await db.commit()
        return []

    total_qty = sum(qty for _, _, qty in contributor_rows)

    # Ascending by qty: the LARGEST contributor is processed last and
    # absorbs the rounding remainder, not the smallest. Every other
    # farmer's share is a clean round-down of their exact proportion, so
    # nobody's payout is inflated above their true share - only the top
    # contributor's is (by at most a few kobo), which is the more
    # defensible place for rounding slack to land.
    ordered_rows = sorted(contributor_rows, key=lambda row: row[2])

    payouts: list[Payout] = []
    allocated = Decimal("0.00")
    for i, (farmer_id, _email, qty) in enumerate(ordered_rows):
        if i == len(ordered_rows) - 1:
            # Last (largest) farmer gets exactly what's left - guarantees
            # the sum is exact regardless of how rounding fell for
            # everyone else.
            amount = total_revenue - allocated
        else:
            raw_share = total_revenue * Decimal(qty) / Decimal(total_qty)
            amount = raw_share.quantize(_CENTS, rounding=ROUND_DOWN)
            allocated += amount
        payout = Payout(pool_id=pool_id, farmer_id=farmer_id, amount=amount)
        db.add(payout)
        payouts.append(payout)

    try:
        await db.commit()
    except IntegrityError:
        # Guard #2 - see module docstring.
        await db.rollback()
        raise PoolAlreadyPaidOut(pool_id)

    for p in payouts:
        await db.refresh(p)
    return payouts


async def list_payouts_for_pool(db: AsyncSession, pool_id: uuid.UUID) -> list[Payout]:
    result = await db.execute(
        select(Payout).where(Payout.pool_id == pool_id).order_by(Payout.created_at.desc())
    )
    return list(result.scalars().all())


async def list_payouts_for_farmer(db: AsyncSession, farmer_id: uuid.UUID) -> list[Payout]:
    result = await db.execute(
        select(Payout).where(Payout.farmer_id == farmer_id).order_by(Payout.created_at.desc())
    )
    return list(result.scalars().all())
