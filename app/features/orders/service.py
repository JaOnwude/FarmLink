"""
Orders feature - business logic, called by router.py.

This is "the hard problem" from the brief: the pool row is the single
source of truth for what's left to sell. SELECT ... FOR UPDATE locks that
row for the whole transaction, so when two buyers race for the same
stock, the second one's SELECT blocks until the first COMMITs, then sees
the ALREADY-REDUCED available_qty and correctly fails with 409 instead of
both succeeding and taking the pool negative.

Order of checks matters and mirrors the flowchart exactly:
  1. idempotency check (cheap, no lock needed yet)
  2. lock the pool row
  3. pool exists?          -> 404
  4. pool OPEN?            -> 409 (ROLLBACK)
  5. available_qty >= qty? -> 409 (ROLLBACK)
  6. mutate + insert + COMMIT
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.features.orders.models import Allocation, Order, OrderStatus
from app.features.pools.models import Pool, PoolStatus
from app.features.pools.service import invalidate_pool_cache

logger = logging.getLogger("farmlink.sweep")


class PoolNotFound(Exception):
    pass


class PoolClosed(Exception):
    pass


class InsufficientStock(Exception):
    pass


async def _find_by_idempotency_key(
    db: AsyncSession, buyer_id: uuid.UUID, idempotency_key: str
) -> Order | None:
    result = await db.execute(
        select(Order).where(Order.buyer_id == buyer_id, Order.idempotency_key == idempotency_key)
    )
    return result.scalar_one_or_none()


async def create_order(
    db: AsyncSession,
    buyer_id: uuid.UUID,
    pool_id: uuid.UUID,
    qty: int,
    idempotency_key: str | None = None,
) -> Order:
    # Cheap short-circuit for the common retry case: same buyer, same key,
    # request already succeeded once. No lock needed for this check - it's
    # a plain read, and a genuine race on the SAME key is handled below by
    # the DB's own unique constraint, not by this check.
    if idempotency_key is not None:
        existing = await _find_by_idempotency_key(db, buyer_id, idempotency_key)
        if existing is not None:
            return existing

    result = await db.execute(select(Pool).where(Pool.id == pool_id).with_for_update())
    pool = result.scalar_one_or_none()
    if pool is None:
        await db.rollback()
        raise PoolNotFound(pool_id)

    if pool.status != PoolStatus.OPEN:
        await db.rollback()
        raise PoolClosed(pool_id)

    if pool.available_qty < qty:
        await db.rollback()
        raise InsufficientStock(pool_id)

    total: Decimal = pool.price * qty
    pool.available_qty -= qty

    order = Order(
        pool_id=pool_id,
        buyer_id=buyer_id,
        qty=qty,
        total=total,
        status=OrderStatus.PENDING,
        idempotency_key=idempotency_key,
    )
    db.add(order)
    try:
        await db.flush()
    except IntegrityError:
        # Two requests with the SAME idempotency key raced past the check
        # above before either committed - the unique index catches what
        # the read-then-write check couldn't. Whichever one loses the race
        # here isn't a real failure: return the order the winner created.
        await db.rollback()
        if idempotency_key is not None:
            existing = await _find_by_idempotency_key(db, buyer_id, idempotency_key)
            if existing is not None:
                return existing
        raise

    allocation = Allocation(order_id=order.id, pool_id=pool_id, qty=qty)
    db.add(allocation)

    await db.commit()
    await db.refresh(order)
    # An order changes available_qty on the pool - same reasoning as
    # contributions: invalidate immediately rather than waiting on TTL.
    await invalidate_pool_cache(pool_id)
    return order


async def get_order(db: AsyncSession, order_id: uuid.UUID) -> Order:
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if order is None:
        raise ValueError(order_id)
    return order


async def list_orders_for_buyer(db: AsyncSession, buyer_id: uuid.UUID) -> list[Order]:
    result = await db.execute(
        select(Order).where(Order.buyer_id == buyer_id).order_by(Order.created_at.desc())
    )
    return list(result.scalars().all())


async def _release_one_expired_order(order_id: uuid.UUID, cutoff: datetime) -> bool:
    """One order, one transaction. Returns True if this order was
    actually released, False if it turned out not to need releasing
    (already paid/expired by the time we got the lock, or no longer past
    cutoff - re-checked here, not trusted from the earlier candidate
    query, because time has passed since that query ran).

    Lock ORDER first, not just the pool: this is what actually closes the
    race with Day 8's webhook. If a payment succeeds at the exact moment
    the sweep is about to expire the same order, whichever of the two
    acquires the order row's lock first wins, and the other sees the
    already-updated status when it re-checks - exactly the same pattern
    Day 5 uses for the pool row, applied here to the order row instead.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Order).where(Order.id == order_id).with_for_update())
        order = result.scalar_one_or_none()
        if order is None:
            return False
        if order.status != OrderStatus.PENDING:
            # Paid, already expired, or cancelled since the candidate
            # query ran - most commonly: the webhook won the race.
            return False
        if order.created_at >= cutoff:
            # No longer actually past the window (cutoff is computed once
            # per sweep run, so this only matters for an order that was
            # borderline at query time).
            return False

        pool_result = await db.execute(
            select(Pool).where(Pool.id == order.pool_id).with_for_update()
        )
        pool = pool_result.scalar_one_or_none()
        if pool is not None:
            pool.available_qty += order.qty

        order.status = OrderStatus.EXPIRED
        await db.commit()

    if pool is not None:
        await invalidate_pool_cache(pool.id)
    return True


async def sweep_expired_orders(
    max_age_hours: int | None = None,
) -> int:
    """Implements the brief's 24h sweep: 'A scheduled sweep releases
    unpaid orders after 24 hours by adding the quantity back - also
    under the lock.' Runs on its own schedule (Day 9's APScheduler job),
    never triggered by user request traffic - a sweep is background
    maintenance, not something a buyer's page load should be able to
    kick off or block on.

    Each candidate order gets its OWN transaction rather than one giant
    transaction for the whole batch: a lock held on every stale order's
    pool simultaneously would block real traffic for as long as the sweep
    takes, and one bad row shouldn't roll back released quota for every
    other order in the same run.
    """
    from app.core.config import get_settings

    settings = get_settings()
    hours = max_age_hours if max_age_hours is not None else settings.sweep_order_max_age_hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Order.id).where(Order.status == OrderStatus.PENDING, Order.created_at < cutoff)
        )
        candidate_ids = [row[0] for row in result.all()]

    released_count = 0
    for order_id in candidate_ids:
        try:
            if await _release_one_expired_order(order_id, cutoff):
                released_count += 1
        except Exception:
            # One bad order shouldn't stop the sweep from processing the
            # rest of the batch - log it and move on. Real observability
            # (Day 13) is what makes this loud enough to actually notice.
            logger.exception(f"Sweep failed to release order {order_id}")

    if released_count:
        logger.info(f"Sweep released {released_count} expired order(s)")
    return released_count
