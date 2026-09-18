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
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.orders.models import Allocation, Order, OrderStatus
from app.features.pools.models import Pool, PoolStatus


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
