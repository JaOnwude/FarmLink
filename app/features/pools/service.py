"""
Pools feature - business logic, called by router.py.

Cache-aside on the two read paths (list, get-by-id): check Redis first,
fall through to Postgres on a miss, write what was read back into Redis.
The pools feature owns this cache because it owns the Pool entity - other
features that mutate a pool (contributions, orders) import
invalidate_pool_cache() from here rather than reimplementing cache-key
knowledge in their own service.py.

Correctness matters more than hit rate for this specific cache: a pool's
available_qty changes on every order, so returning a stale count risks a
buyer trying to order stock that's already gone (they'd still get a
correct 409 from the locked transaction - the cache only feeds
GET/browsing, never the order-placement path itself, which always reads
Postgres directly under FOR UPDATE). That's why every write below calls
invalidate_pool_cache() rather than leaning on the 30s TTL alone.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, cache_get, cache_set
from app.core.config import get_settings
from app.features.feed.service import publish_pool_event
from app.features.pools.models import Pool, PoolStatus
from app.features.pools.schemas import PoolCreate, PoolOut

settings = get_settings()


class PoolNotFound(Exception):
    pass


class PoolAlreadyClosed(Exception):
    pass


def _list_cache_key(status_filter: PoolStatus | None) -> str:
    # Fixed, known set of keys (all / open / closed) rather than an
    # arbitrary pattern - lets invalidation delete exact keys instead of
    # needing a Redis KEYS/SCAN, which is the right call to avoid in a
    # shared production Redis instance.
    suffix = status_filter.value if status_filter is not None else "all"
    return f"pools:list:{suffix}"


def _pool_cache_key(pool_id: uuid.UUID) -> str:
    return f"pools:{pool_id}"


async def invalidate_pool_cache(pool_id: uuid.UUID | None = None) -> None:
    """Called after every write that touches a pool row - creation,
    close, a contribution changing its quantities, an order changing its
    quantities. Deletes all three list-cache variants unconditionally
    (cheap - 3 keys) plus the specific pool's own cache entry if given."""
    keys = [_list_cache_key(None), _list_cache_key(PoolStatus.OPEN), _list_cache_key(PoolStatus.CLOSED)]
    if pool_id is not None:
        keys.append(_pool_cache_key(pool_id))
    await cache_delete(*keys)


async def create_pool(db: AsyncSession, data: PoolCreate) -> Pool:
    pool = Pool(
        product=data.product,
        price=data.price,
        unit=data.unit,
        total_qty=0,
        available_qty=0,
        status=PoolStatus.OPEN,
    )
    db.add(pool)
    await db.commit()
    await db.refresh(pool)
    await invalidate_pool_cache()
    return pool


async def list_pools(db: AsyncSession, status_filter: PoolStatus | None = None) -> list[PoolOut]:
    cache_key = _list_cache_key(status_filter)
    cached = await cache_get(cache_key)
    if cached is not None:
        return [PoolOut.model_validate(item) for item in cached]

    stmt = select(Pool).order_by(Pool.created_at.desc())
    if status_filter is not None:
        stmt = stmt.where(Pool.status == status_filter)
    result = await db.execute(stmt)
    pools = [PoolOut.model_validate(p, from_attributes=True) for p in result.scalars().all()]

    await cache_set(
        cache_key,
        [p.model_dump(mode="json") for p in pools],
        ttl_seconds=settings.pool_cache_ttl_seconds,
    )
    return pools


async def get_pool(db: AsyncSession, pool_id: uuid.UUID) -> PoolOut:
    cache_key = _pool_cache_key(pool_id)
    cached = await cache_get(cache_key)
    if cached is not None:
        return PoolOut.model_validate(cached)

    result = await db.execute(select(Pool).where(Pool.id == pool_id))
    pool = result.scalar_one_or_none()
    if pool is None:
        raise PoolNotFound(pool_id)

    pool_out = PoolOut.model_validate(pool, from_attributes=True)
    await cache_set(
        cache_key, pool_out.model_dump(mode="json"), ttl_seconds=settings.pool_cache_ttl_seconds
    )
    return pool_out


async def close_pool(db: AsyncSession, pool_id: uuid.UUID) -> Pool:
    """Admin manually closes a pool once they judge enough has been
    contributed / sold. Locked the same way the order flow locks a pool -
    closing while an order is mid-flight on the same row waits for that
    transaction rather than racing it."""
    result = await db.execute(select(Pool).where(Pool.id == pool_id).with_for_update())
    pool = result.scalar_one_or_none()
    if pool is None:
        await db.rollback()
        raise PoolNotFound(pool_id)
    if pool.status == PoolStatus.CLOSED:
        await db.rollback()
        raise PoolAlreadyClosed(pool_id)
    pool.status = PoolStatus.CLOSED
    await db.commit()
    await db.refresh(pool)
    await invalidate_pool_cache(pool_id)
    await publish_pool_event(pool_id, "pool.closed", {})
    return pool
