"""
Orders feature - business logic, called by router.py.

This module handles the trickiest concurrency problem in the app: many
buyers can try to order from the same pool of stock at the same time, and
the pool must never be sold below zero. The pool row is treated as the
single source of truth for what's left to sell, and `SELECT ... FOR
UPDATE` locks that row for the whole transaction. So when two buyers race
for the same stock, the second buyer's SELECT blocks until the first
buyer's transaction COMMITs, then it sees the ALREADY-REDUCED
available_qty and correctly fails with a 409 Conflict instead of both
requests succeeding and taking the pool negative.

The order in which checks happen matters and is deliberate:
  1. idempotency check (cheap, plain read, no row lock needed yet)
  2. lock the pool row (SELECT ... FOR UPDATE)
  3. does the pool exist?              -> 404 if not
  4. is the pool still OPEN?           -> 409 + ROLLBACK if not
  5. is there enough available_qty?    -> 409 + ROLLBACK if not
  6. mutate the pool, insert the order, COMMIT
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.jobs import enqueue
from app.features.feed.service import publish_pool_event
from app.features.orders.models import Allocation, Order, OrderStatus
from app.features.orders.tasks import notify_admin_of_new_order, send_order_confirmation_email
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
    buyer_email: str,
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

    # Price is snapshotted onto the order at creation time (not looked up
    # again later), so a later price change on the pool never retroactively
    # changes what an already-placed order is charged.
    total: Decimal = pool.price * qty
    # Safe to mutate directly: we're still holding the row lock acquired by
    # the SELECT ... FOR UPDATE above, so no other transaction can be
    # reading or writing this same row concurrently.
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

    # Background jobs - enqueued into Redis, not awaited inline. Sending an
    # email or pinging an admin isn't part of what makes the order valid,
    # so there's no reason to make the buyer's request wait on it. It also
    # means these still run even if the API process restarts right after
    # this line: the job is sitting in Redis, not held in this process's
    # memory, so a worker process can pick it up independently.
    enqueue(send_order_confirmation_email, buyer_email, str(order.id), qty, str(total))
    enqueue(notify_admin_of_new_order, str(order.id), str(pool_id), buyer_email, qty)
    await publish_pool_event(
        pool_id,
        "order.placed",
        {"order_id": str(order.id), "qty": qty, "available_qty": pool.available_qty},
    )

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

    Lock the ORDER row first, not just the pool row: this is what actually
    closes the race against the payment webhook. If a payment succeeds at
    the exact moment the sweep is about to expire the same order, whichever
    of the two acquires the order row's lock first wins, and the other one
    sees the already-updated status when it re-checks afterwards - the
    same SELECT ... FOR UPDATE pattern used for the pool row in
    create_order() above, applied here to the order row instead.
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
        await publish_pool_event(
            pool.id,
            "order.expired",
            {"order_id": str(order_id), "qty": order.qty, "available_qty": pool.available_qty},
        )
    return True


async def sweep_expired_orders(
    max_age_hours: int | None = None,
) -> int:
    """Finds every order that's been sitting in PENDING (created but never
    paid for) past the configured age limit, and releases the quantity it
    was holding back to the pool. Runs on its own APScheduler timer (wired
    up in main.py's lifespan), never triggered by user request traffic - a
    sweep is background maintenance, not something a buyer's page load
    should be able to kick off or block on.

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
            # rest of the batch - log it and move on to the next candidate.
            # Structured logging with the failing order_id is what makes
            # this loud enough to actually notice and investigate later,
            # instead of silently swallowing the failure.
            logger.exception(f"Sweep failed to release order {order_id}")

    if released_count:
        logger.info(f"Sweep released {released_count} expired order(s)")
    return released_count
