"""
Day 8 tests.

initialize_payment tests mock app.features.payments.paystack_client -
the ONE boundary that talks to the real internet - rather than hitting
api.paystack.co, which this sandbox can't reach anyway and a unit test
never should.

Webhook tests build REAL HMAC-SHA512 signatures against
settings.paystack_secret_key and post raw bytes, exercising the same
four scenarios the brief's mock_payment_provider.py script checks:
valid event, duplicate delivery, forged signature, orphan reference.
"""
import hashlib
import hmac
import json
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token, hash_password
from app.features.auth.models import User, UserRole
from app.features.orders.models import Order, OrderStatus
from app.features.payments.models import Payment
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


async def _make_pool_with_stock(admin: User, farmer: User, client: AsyncClient, qty: int) -> str:
    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Rice", "price": "45000.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": qty},
        headers=_auth_header(farmer),
    )
    return pool_id


async def _make_pending_order(client: AsyncClient, admin, farmer, buyer, qty=10) -> str:
    pool_id = await _make_pool_with_stock(admin, farmer, client, qty=qty + 5)
    order_resp = await client.post(
        "/api/v1/orders", json={"pool_id": pool_id, "qty": qty}, headers=_auth_header(buyer)
    )
    return order_resp.json()["id"]


def _sign(body: bytes) -> str:
    return hmac.new(settings.paystack_secret_key.encode("utf-8"), body, hashlib.sha512).hexdigest()


def _paystack_event(event_type: str, reference: str, amount_kobo: int) -> bytes:
    # A fresh, unique event id every call - NOT a fixed test constant.
    # Postgres here persists across separate pytest invocations (it's a
    # real dev database, not a throwaway per-run one), so a hardcoded id
    # like 1001 collides with the SAME id inserted by an earlier run and
    # gets - correctly - treated as an already-processed duplicate,
    # which silently breaks an unrelated "first delivery" test.
    event_id = f"evt-{uuid.uuid4().hex[:16]}"
    payload = {
        "event": event_type,
        "data": {
            "id": event_id,
            "reference": reference,
            "amount": amount_kobo,
            "status": "success",
            "currency": "NGN",
        },
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


@pytest.mark.asyncio
async def test_initialize_payment_calls_paystack_and_returns_its_response(
    client: AsyncClient, monkeypatch
):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)
    order_id = await _make_pending_order(client, admin, farmer, buyer, qty=10)

    captured = {}

    async def fake_initialize_transaction(email, amount_kobo, reference, callback_url=None):
        captured["email"] = email
        captured["amount_kobo"] = amount_kobo
        captured["reference"] = reference
        return {
            "authorization_url": "https://checkout.paystack.com/fake123",
            "access_code": "fake_access_code",
            "reference": reference,
        }

    monkeypatch.setattr(
        "app.features.payments.service.paystack_client.initialize_transaction",
        fake_initialize_transaction,
    )

    resp = await client.post(
        "/api/v1/payments/initialize", json={"order_id": order_id}, headers=_auth_header(buyer)
    )
    assert resp.status_code == 200
    assert resp.json()["authorization_url"] == "https://checkout.paystack.com/fake123"
    assert captured["email"] == buyer.email
    assert captured["reference"] == order_id
    assert captured["amount_kobo"] == 45000 * 10 * 100  # price * qty, in kobo


@pytest.mark.asyncio
async def test_cannot_initialize_payment_for_another_buyers_order(
    client: AsyncClient, monkeypatch
):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer_a = await _make_user(UserRole.BUYER)
    buyer_b = await _make_user(UserRole.BUYER)
    order_id = await _make_pending_order(client, admin, farmer, buyer_a, qty=5)

    resp = await client.post(
        "/api/v1/payments/initialize", json={"order_id": order_id}, headers=_auth_header(buyer_b)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cannot_initialize_payment_for_already_paid_order(client: AsyncClient, monkeypatch):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)
    order_id = await _make_pending_order(client, admin, farmer, buyer, qty=5)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Order).where(Order.id == uuid.UUID(order_id)))
        order = result.scalar_one()
        order.status = OrderStatus.PAID
        await db.commit()

    resp = await client.post(
        "/api/v1/payments/initialize", json={"order_id": order_id}, headers=_auth_header(buyer)
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_webhook_valid_event_marks_order_paid(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)
    order_id = await _make_pending_order(client, admin, farmer, buyer, qty=10)

    body = _paystack_event("charge.success", order_id, 45000000)
    resp = await client.post(
        "/api/v1/payments/webhook",
        content=body,
        headers={"x-paystack-signature": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 200

    order_resp = await client.get(f"/api/v1/orders/{order_id}", headers=_auth_header(buyer))
    assert order_resp.json()["status"] == "paid"

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Payment).where(Payment.order_id == uuid.UUID(order_id)))
        payment = result.scalar_one()
        assert payment.amount == Decimal("450000.00")


@pytest.mark.asyncio
async def test_webhook_duplicate_event_does_not_double_process(client: AsyncClient):
    """Mirrors mock_payment_provider.py's '2. SAME event again (retry)'
    scenario: 200 both times, nothing changes on the second delivery."""
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)
    order_id = await _make_pending_order(client, admin, farmer, buyer, qty=10)

    body = _paystack_event("charge.success", order_id, 45000000)
    signature = _sign(body)

    first = await client.post(
        "/api/v1/payments/webhook",
        content=body,
        headers={"x-paystack-signature": signature, "Content-Type": "application/json"},
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/v1/payments/webhook",
        content=body,
        headers={"x-paystack-signature": signature, "Content-Type": "application/json"},
    )
    assert second.status_code == 200

    async with AsyncSessionLocal() as db:
        count = await db.scalar(
            select(func.count()).select_from(Payment).where(Payment.order_id == uuid.UUID(order_id))
        )
        assert count == 1  # exactly one payment row, not two


@pytest.mark.asyncio
async def test_webhook_wrong_signature_is_401(client: AsyncClient):
    """Mirrors mock_payment_provider.py's '3. wrong signature' scenario: 401."""
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)
    order_id = await _make_pending_order(client, admin, farmer, buyer, qty=10)

    body = _paystack_event("charge.success", order_id, 45000000)
    resp = await client.post(
        "/api/v1/payments/webhook",
        content=body,
        headers={"x-paystack-signature": "deadbeef" * 16, "Content-Type": "application/json"},
    )
    assert resp.status_code == 401

    order_resp = await client.get(f"/api/v1/orders/{order_id}", headers=_auth_header(buyer))
    assert order_resp.json()["status"] == "pending"  # unaffected by the forged event


@pytest.mark.asyncio
async def test_webhook_orphan_reference_is_200_and_logged_not_errored(client: AsyncClient):
    """Mirrors mock_payment_provider.py's '4. unknown reference' scenario:
    200, no matching order, no crash."""
    fake_reference = str(uuid.uuid4())
    body = _paystack_event("charge.success", fake_reference, 10000)
    resp = await client.post(
        "/api/v1/payments/webhook",
        content=body,
        headers={"x-paystack-signature": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_non_charge_success_event_is_recorded_but_inert(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)
    order_id = await _make_pending_order(client, admin, farmer, buyer, qty=10)

    body = _paystack_event("charge.failed", order_id, 45000000)
    resp = await client.post(
        "/api/v1/payments/webhook",
        content=body,
        headers={"x-paystack-signature": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 200

    order_resp = await client.get(f"/api/v1/orders/{order_id}", headers=_auth_header(buyer))
    assert order_resp.json()["status"] == "pending"  # a failed charge never marks it paid
