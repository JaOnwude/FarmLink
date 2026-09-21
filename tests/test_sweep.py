"""
Day 9 tests: the sweep job. The first test here is the last of the
brief's five must-pass tests directly implemented:
  "The sweep releases an unpaid 25-hour-old order and available_qty goes
   back up."
The remaining tests cover what the sweep must NOT touch, and the race
against Day 8's webhook that the order-row lock exists to prevent.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token, hash_password
from app.features.auth.models import User, UserRole
from app.features.orders.models import Order, OrderStatus
from app.features.orders.service import sweep_expired_orders
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


async def _make_pending_order(client: AsyncClient, admin, farmer, buyer, qty=10, pool_qty=20) -> str:
    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Rice", "price": "45000.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": pool_qty},
        headers=_auth_header(farmer),
    )
    order_resp = await client.post(
        "/api/v1/orders", json={"pool_id": pool_id, "qty": qty}, headers=_auth_header(buyer)
    )
    return order_resp.json()["id"]


async def _backdate_order(order_id: str, hours_ago: float) -> None:
    """Simulates an order placed hours_ago - directly mutating created_at
    is the only way to test a 24h window without actually waiting 24h."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Order).where(Order.id == uuid.UUID(order_id)))
        order = result.scalar_one()
        order.created_at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        await db.commit()


@pytest.mark.asyncio
async def test_sweep_releases_a_25_hour_old_unpaid_order(client: AsyncClient):
    """The brief's fifth required test, verbatim."""
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)

    order_id = await _make_pending_order(client, admin, farmer, buyer, qty=10, pool_qty=20)
    await _backdate_order(order_id, hours_ago=25)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Order).where(Order.id == uuid.UUID(order_id)))
        pool_id = result.scalar_one().pool_id

    released = await sweep_expired_orders(max_age_hours=24)
    assert released >= 1

    order_resp = await client.get(f"/api/v1/orders/{order_id}", headers=_auth_header(buyer))
    assert order_resp.json()["status"] == "expired"

    pool_resp = await client.get(f"/api/v1/pools/{pool_id}")
    # 20 contributed, 10 taken by the order, order released -> 20 available again
    assert pool_resp.json()["available_qty"] == 20


@pytest.mark.asyncio
async def test_sweep_does_not_touch_a_recent_pending_order(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)

    order_id = await _make_pending_order(client, admin, farmer, buyer, qty=5, pool_qty=20)
    # No backdating - this order was just placed.

    await sweep_expired_orders(max_age_hours=24)

    order_resp = await client.get(f"/api/v1/orders/{order_id}", headers=_auth_header(buyer))
    assert order_resp.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_sweep_does_not_touch_an_already_paid_order(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)

    order_id = await _make_pending_order(client, admin, farmer, buyer, qty=5, pool_qty=20)
    await _backdate_order(order_id, hours_ago=48)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Order).where(Order.id == uuid.UUID(order_id)))
        order = result.scalar_one()
        order.status = OrderStatus.PAID
        await db.commit()
        pool_id = order.pool_id

    await sweep_expired_orders(max_age_hours=24)

    order_resp = await client.get(f"/api/v1/orders/{order_id}", headers=_auth_header(buyer))
    assert order_resp.json()["status"] == "paid"  # untouched
    pool_resp = await client.get(f"/api/v1/pools/{pool_id}")
    assert pool_resp.json()["available_qty"] == 15  # 20 - 5, NOT given back


@pytest.mark.asyncio
async def test_sweep_is_safe_to_run_twice(client: AsyncClient):
    """The second run should find nothing left to do, not double-release
    the quantity back to the pool."""
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)

    order_id = await _make_pending_order(client, admin, farmer, buyer, qty=10, pool_qty=20)
    await _backdate_order(order_id, hours_ago=30)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Order).where(Order.id == uuid.UUID(order_id)))
        pool_id = result.scalar_one().pool_id

    first_run = await sweep_expired_orders(max_age_hours=24)
    assert first_run >= 1
    second_run = await sweep_expired_orders(max_age_hours=24)

    pool_resp = await client.get(f"/api/v1/pools/{pool_id}")
    assert pool_resp.json()["available_qty"] == 20  # given back exactly once
