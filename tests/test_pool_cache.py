"""
Day 6 tests. The bar here isn't "does it crash" - it's proving the cache
is genuinely being read (by mutating the DB directly, bypassing the app,
and confirming a cached response still shows the OLD value), and that
invalidation genuinely clears it (confirming the NEXT call after a write
shows the NEW value instead of waiting out the TTL).
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.core.database import AsyncSessionLocal
from app.core.redis_client import redis_client
from app.core.security import create_access_token, hash_password
from app.features.auth.models import User, UserRole
from app.features.pools.models import Pool
from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _make_user(role: UserRole, approved: bool = True) -> User:
    async with AsyncSessionLocal() as db:
        user = User(
            email=f"{role.value}-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("whatever-12345"),
            role=role,
            approved=approved,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


def _auth_header(user: User) -> dict:
    token = create_access_token(user.id, user.role)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_get_pool_is_actually_served_from_cache(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    create_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Cache Test Product", "price": "1000.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    pool_id = create_resp.json()["id"]

    # First read - populates the cache.
    first = await client.get(f"/api/v1/pools/{pool_id}")
    assert first.status_code == 200

    cache_key = f"cache:pools:{pool_id}"
    cached_raw = await redis_client.get(cache_key)
    assert cached_raw is not None, "expected a cache entry to exist after the first read"
    ttl = await redis_client.ttl(cache_key)
    assert 0 < ttl <= 30

    # Mutate the DB directly, bypassing the app entirely - if the next
    # read still shows the OLD price, that proves it came from cache, not
    # a fresh query (a fresh query would see this change immediately).
    async with AsyncSessionLocal() as db:
        await db.execute(update(Pool).where(Pool.id == uuid.UUID(pool_id)).values(price="9999.00"))
        await db.commit()

    second = await client.get(f"/api/v1/pools/{pool_id}")
    assert second.json()["price"] == "1000.00", "cache should still be serving the stale value"


@pytest.mark.asyncio
async def test_list_pools_cache_invalidated_by_contribution(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)

    create_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Invalidation Test Product", "price": "2000.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    pool_id = create_resp.json()["id"]

    first_list = await client.get("/api/v1/pools")
    assert any(p["id"] == pool_id and p["available_qty"] == 0 for p in first_list.json())

    # This should invalidate the list cache - verified below by checking
    # the very next list call reflects the new quantity immediately,
    # rather than the pre-contribution cached value.
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 12},
        headers=_auth_header(farmer),
    )

    second_list = await client.get("/api/v1/pools")
    matching = [p for p in second_list.json() if p["id"] == pool_id]
    assert matching and matching[0]["available_qty"] == 12


@pytest.mark.asyncio
async def test_pool_cache_invalidated_by_order(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)

    create_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Order Invalidation Product", "price": "3000.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    pool_id = create_resp.json()["id"]
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 20},
        headers=_auth_header(farmer),
    )

    # Populate the single-pool cache at available_qty == 20.
    cached_before = await client.get(f"/api/v1/pools/{pool_id}")
    assert cached_before.json()["available_qty"] == 20

    await client.post(
        "/api/v1/orders", json={"pool_id": pool_id, "qty": 8}, headers=_auth_header(buyer)
    )

    cached_after = await client.get(f"/api/v1/pools/{pool_id}")
    assert cached_after.json()["available_qty"] == 12, (
        "order placement must invalidate the pool's cache entry, not leave it "
        "showing pre-order stock"
    )
