"""
Day 5 tests: the hard problem. These are three of the brief's five
must-pass tests:
  - Two orders for the last 40 bags (30 each) at the same time -> one 201, one 409.
  - An exact-fit order succeeds; an over-order -> 409; total allocated never
    exceeds total contributed.
  - Closed pool rejects orders with 409.
Plus idempotency (a Day 5 addition beyond the brief's five, protecting
against retried requests).
"""
import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token, hash_password
from app.features.auth.models import User, UserRole
from app.features.contributions.models import Contribution
from app.features.orders.models import Allocation
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


async def _make_pool_with_stock(admin: User, farmer: User, client: AsyncClient, qty: int) -> str:
    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Rice (50kg bags)", "price": "45000.00"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": qty},
        headers=_auth_header(farmer),
    )
    return pool_id


@pytest.mark.asyncio
async def test_two_concurrent_orders_for_last_40_bags_one_wins_one_409(client: AsyncClient):
    """The brief's headline concurrency test. Two genuinely concurrent
    requests (asyncio.gather, not sequential awaits) race for the same
    pool row - the SELECT ... FOR UPDATE lock must serialize them so the
    second one sees the already-reduced available_qty, not the stale
    pre-lock value."""
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer_a = await _make_user(UserRole.BUYER)
    buyer_b = await _make_user(UserRole.BUYER)

    pool_id = await _make_pool_with_stock(admin, farmer, client, qty=40)

    async def place_order(buyer: User):
        return await client.post(
            "/api/v1/orders",
            json={"pool_id": pool_id, "qty": 30},
            headers=_auth_header(buyer),
        )

    resp_a, resp_b = await asyncio.gather(place_order(buyer_a), place_order(buyer_b))

    statuses = sorted([resp_a.status_code, resp_b.status_code])
    assert statuses == [201, 409]

    pool_after = await client.get(f"/api/v1/pools/{pool_id}")
    assert pool_after.json()["available_qty"] == 10  # 40 - 30, the loser never touched it


@pytest.mark.asyncio
async def test_exact_fit_order_succeeds_then_pool_is_empty(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)

    pool_id = await _make_pool_with_stock(admin, farmer, client, qty=25)

    resp = await client.post(
        "/api/v1/orders", json={"pool_id": pool_id, "qty": 25}, headers=_auth_header(buyer)
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"

    pool_after = await client.get(f"/api/v1/pools/{pool_id}")
    assert pool_after.json()["available_qty"] == 0


@pytest.mark.asyncio
async def test_over_order_is_409_and_allocations_never_exceed_contributions(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)

    pool_id = await _make_pool_with_stock(admin, farmer, client, qty=10)

    over_resp = await client.post(
        "/api/v1/orders", json={"pool_id": pool_id, "qty": 11}, headers=_auth_header(buyer)
    )
    assert over_resp.status_code == 409

    ok_resp = await client.post(
        "/api/v1/orders", json={"pool_id": pool_id, "qty": 10}, headers=_auth_header(buyer)
    )
    assert ok_resp.status_code == 201

    async with AsyncSessionLocal() as db:
        total_contributed = await db.scalar(
            select(func.coalesce(func.sum(Contribution.qty), 0)).where(
                Contribution.pool_id == uuid.UUID(pool_id)
            )
        )
        total_allocated = await db.scalar(
            select(func.coalesce(func.sum(Allocation.qty), 0)).where(
                Allocation.pool_id == uuid.UUID(pool_id)
            )
        )
    assert total_allocated <= total_contributed
    assert total_allocated == 10


@pytest.mark.asyncio
async def test_closed_pool_rejects_orders_with_409(client: AsyncClient):
    """Orders-side twin of Day 4's closed-pool contribution test."""
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)

    pool_id = await _make_pool_with_stock(admin, farmer, client, qty=20)
    await client.patch(f"/api/v1/pools/{pool_id}/close", headers=_auth_header(admin))

    resp = await client.post(
        "/api/v1/orders", json={"pool_id": pool_id, "qty": 5}, headers=_auth_header(buyer)
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_retried_order_with_same_idempotency_key_does_not_double_book(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)

    pool_id = await _make_pool_with_stock(admin, farmer, client, qty=20)
    key = str(uuid.uuid4())

    first = await client.post(
        "/api/v1/orders",
        json={"pool_id": pool_id, "qty": 15},
        headers={**_auth_header(buyer), "Idempotency-Key": key},
    )
    assert first.status_code == 201
    first_order_id = first.json()["id"]

    # Simulate a client retry after a dropped response - same key, same request.
    retry = await client.post(
        "/api/v1/orders",
        json={"pool_id": pool_id, "qty": 15},
        headers={**_auth_header(buyer), "Idempotency-Key": key},
    )
    assert retry.status_code == 201
    assert retry.json()["id"] == first_order_id  # same order returned, not a new one

    pool_after = await client.get(f"/api/v1/pools/{pool_id}")
    assert pool_after.json()["available_qty"] == 5  # only debited once, not twice


@pytest.mark.asyncio
async def test_buyer_cannot_view_another_buyers_order(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer_a = await _make_user(UserRole.BUYER)
    buyer_b = await _make_user(UserRole.BUYER)

    pool_id = await _make_pool_with_stock(admin, farmer, client, qty=20)
    order_resp = await client.post(
        "/api/v1/orders", json={"pool_id": pool_id, "qty": 5}, headers=_auth_header(buyer_a)
    )
    order_id = order_resp.json()["id"]

    resp = await client.get(f"/api/v1/orders/{order_id}", headers=_auth_header(buyer_b))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_farmer_cannot_place_order(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)

    pool_id = await _make_pool_with_stock(admin, farmer, client, qty=20)
    resp = await client.post(
        "/api/v1/orders", json={"pool_id": pool_id, "qty": 5}, headers=_auth_header(farmer)
    )
    assert resp.status_code == 403
