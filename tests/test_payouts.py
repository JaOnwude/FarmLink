"""
Day 12 tests: payouts. The first test here is the brief's fifth and
final required test: "Payout amounts sum to revenue; a second share-out
-> 409."

Getting an actual rounding edge case requires care: if the order buys
exactly ALL of the contributed stock, revenue / total_contributed
reduces to just the unit price, which divides evenly no matter what the
contribution split is - that arithmetic accident would let a naive,
buggy rounding implementation pass undetected. The test below therefore
buys only PART of the pool (1 of 3 contributed units), so revenue does
NOT divide evenly across the 1:2 contribution split - only an
implementation that actually handles remainder cents correctly passes.
"""
import hashlib
import hmac
import json
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token, hash_password
from app.features.auth.models import User, UserRole
from app.main import app

settings = get_settings()


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


def _sign(body: bytes) -> str:
    return hmac.new(settings.paystack_secret_key.encode("utf-8"), body, hashlib.sha512).hexdigest()


async def _pay_for_order(client: AsyncClient, order_id: str, amount_kobo: int) -> None:
    """Drives the real Day 8 webhook path to genuinely mark an order
    PAID - not a direct DB write - so this test exercises the real
    revenue-recognition path payouts depend on."""
    body = json.dumps(
        {
            "event": "charge.success",
            "data": {
                "id": f"evt-{uuid.uuid4().hex[:12]}",
                "reference": order_id,
                "amount": amount_kobo,
                "status": "success",
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    resp = await client.post(
        "/api/v1/payments/webhook",
        content=body,
        headers={"x-paystack-signature": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_payout_amounts_sum_exactly_to_revenue_with_uneven_rounding(client: AsyncClient):
    """The brief's fifth required test."""
    admin = await _make_user(UserRole.ADMIN)
    farmer_a = await _make_user(UserRole.FARMER)  # contributes 1 share of 3
    farmer_b = await _make_user(UserRole.FARMER)  # contributes 2 shares of 3
    buyer = await _make_user(UserRole.BUYER)

    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Rice", "price": "10.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]

    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 1},
        headers=_auth_header(farmer_a),
    )
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 2},
        headers=_auth_header(farmer_b),
    )

    # Buy and pay for only 1 of the 3 contributed units - revenue (10.00)
    # split 1:2 across contribution shares is 3.33 / 6.67, which does
    # NOT divide evenly to the cent.
    order_resp = await client.post(
        "/api/v1/orders", json={"pool_id": pool_id, "qty": 1}, headers=_auth_header(buyer)
    )
    order_id = order_resp.json()["id"]
    order_total = Decimal(order_resp.json()["total"])  # 10.00
    await _pay_for_order(client, order_id, int(order_total * 100))

    await client.patch(f"/api/v1/pools/{pool_id}/close", headers=_auth_header(admin))

    distribute_resp = await client.post(
        f"/api/v1/payouts/pools/{pool_id}/distribute", headers=_auth_header(admin)
    )
    assert distribute_resp.status_code == 200
    payouts = distribute_resp.json()
    assert len(payouts) == 2

    total_paid_out = sum(Decimal(p["amount"]) for p in payouts)
    assert total_paid_out == order_total  # exact match, not "close enough"

    by_farmer = {p["farmer_id"]: Decimal(p["amount"]) for p in payouts}
    a_amount = by_farmer[str(farmer_a.id)]
    b_amount = by_farmer[str(farmer_b.id)]
    # 1/3 and 2/3 of 10.00 - genuinely non-terminating, so one of these
    # must be 3.33 and the other 6.67 (whichever sorts last by farmer_id
    # gets the leftover cent).
    assert {a_amount, b_amount} == {Decimal("3.33"), Decimal("6.67")}
    assert a_amount + b_amount == Decimal("10.00")


@pytest.mark.asyncio
async def test_second_distribute_attempt_is_409(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)

    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Beans", "price": "5.00", "unit": "basket"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 10},
        headers=_auth_header(farmer),
    )
    order_resp = await client.post(
        "/api/v1/orders", json={"pool_id": pool_id, "qty": 4}, headers=_auth_header(buyer)
    )
    order_id = order_resp.json()["id"]
    await _pay_for_order(client, order_id, 2000)  # 4 * 5.00 = 20.00 -> 2000 kobo

    await client.patch(f"/api/v1/pools/{pool_id}/close", headers=_auth_header(admin))

    first = await client.post(
        f"/api/v1/payouts/pools/{pool_id}/distribute", headers=_auth_header(admin)
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/v1/payouts/pools/{pool_id}/distribute", headers=_auth_header(admin)
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_cannot_distribute_before_pool_is_closed(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)

    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Millet", "price": "4.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 5},
        headers=_auth_header(farmer),
    )
    # Pool is still OPEN - never closed.
    resp = await client.post(
        f"/api/v1/payouts/pools/{pool_id}/distribute", headers=_auth_header(admin)
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_non_admin_cannot_distribute_payouts(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)

    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Sorghum", "price": "3.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]
    await client.patch(f"/api/v1/pools/{pool_id}/close", headers=_auth_header(admin))

    resp = await client.post(
        f"/api/v1/payouts/pools/{pool_id}/distribute", headers=_auth_header(farmer)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_farmer_can_see_only_their_own_payouts(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer_a = await _make_user(UserRole.FARMER)
    farmer_b = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)

    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Cassava", "price": "2.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 5},
        headers=_auth_header(farmer_a),
    )
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 5},
        headers=_auth_header(farmer_b),
    )
    order_resp = await client.post(
        "/api/v1/orders", json={"pool_id": pool_id, "qty": 10}, headers=_auth_header(buyer)
    )
    await _pay_for_order(client, order_resp.json()["id"], 2000)
    await client.patch(f"/api/v1/pools/{pool_id}/close", headers=_auth_header(admin))
    await client.post(f"/api/v1/payouts/pools/{pool_id}/distribute", headers=_auth_header(admin))

    resp = await client.get("/api/v1/payouts/me", headers=_auth_header(farmer_a))
    assert resp.status_code == 200
    payouts = resp.json()
    assert len(payouts) == 1
    assert payouts[0]["farmer_id"] == str(farmer_a.id)
