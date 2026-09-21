"""
Day 10 tests. Two layers deliberately: enqueue tests confirm a job lands
in the real Redis queue with the right arguments; the "processed" tests
go further and actually RUN it via RQ's SimpleWorker in burst mode
(processes whatever's queued, then stops - no long-running worker
needed) to prove the task function itself works, not just that
something got queued.

SimpleWorker (not the default forking Worker) runs jobs in-process,
which is exactly right for a test - no separate OS process to manage,
same reason it's RQ's own documented pattern for testing.
"""
import logging
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from rq import SimpleWorker

from app.core.database import AsyncSessionLocal
from app.core.jobs import job_queue, sync_redis
from app.core.security import create_access_token, hash_password
from app.features.auth.models import User, UserRole
from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _empty_queue_before_and_after():
    """The queue is real Redis, shared across tests in this session -
    empty it before AND after so one test's leftover job can't be picked
    up and misattributed by a later test's SimpleWorker.work(burst=True)."""
    job_queue.empty()
    yield
    job_queue.empty()


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
async def test_placing_an_order_enqueues_two_jobs(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)

    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Rice", "price": "45000.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 20},
        headers=_auth_header(farmer),
    )

    assert len(job_queue) == 0
    order_resp = await client.post(
        "/api/v1/orders", json={"pool_id": pool_id, "qty": 5}, headers=_auth_header(buyer)
    )
    assert order_resp.status_code == 201
    # One job for the buyer's confirmation email, one for admin-notify.
    assert len(job_queue) == 2


@pytest.mark.asyncio
async def test_order_confirmation_job_actually_runs_and_logs(client: AsyncClient, caplog):
    caplog.set_level(logging.INFO, logger="farmlink.jobs.orders")

    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)

    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Yam", "price": "10000.00", "unit": "tuber"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 15},
        headers=_auth_header(farmer),
    )
    order_resp = await client.post(
        "/api/v1/orders", json={"pool_id": pool_id, "qty": 3}, headers=_auth_header(buyer)
    )
    order_id = order_resp.json()["id"]

    # No worker container is running in this test - burst=True runs
    # whatever's already queued, right now, in this process, then stops.
    worker = SimpleWorker([job_queue], connection=sync_redis)
    worker.work(burst=True)

    assert f"Order {order_id} confirmed" in caplog.text
    assert buyer.email in caplog.text
    assert f"New order {order_id}" in caplog.text  # the admin-notify job also ran


@pytest.mark.asyncio
async def test_idempotent_retry_does_not_enqueue_duplicate_jobs(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)

    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Beans", "price": "60000.00", "unit": "basket"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 20},
        headers=_auth_header(farmer),
    )

    key = str(uuid.uuid4())
    headers = {**_auth_header(buyer), "Idempotency-Key": key}
    await client.post("/api/v1/orders", json={"pool_id": pool_id, "qty": 5}, headers=headers)
    assert len(job_queue) == 2

    # Retry with the same key - must NOT enqueue a second round of jobs,
    # or a flaky-connection retry would email the buyer twice.
    await client.post("/api/v1/orders", json={"pool_id": pool_id, "qty": 5}, headers=headers)
    assert len(job_queue) == 2


@pytest.mark.asyncio
async def test_payment_success_enqueues_and_runs_confirmation_job(client: AsyncClient, caplog):
    import hashlib
    import hmac
    import json

    from app.core.config import get_settings

    caplog.set_level(logging.INFO, logger="farmlink.jobs.payments")
    settings = get_settings()

    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)
    buyer = await _make_user(UserRole.BUYER)

    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Maize", "price": "30000.00", "unit": "basket"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 20},
        headers=_auth_header(farmer),
    )
    order_resp = await client.post(
        "/api/v1/orders", json={"pool_id": pool_id, "qty": 10}, headers=_auth_header(buyer)
    )
    order_id = order_resp.json()["id"]

    job_queue.empty()  # order-placement jobs from above aren't what this test checks

    body = json.dumps(
        {
            "event": "charge.success",
            "data": {
                "id": f"evt-{uuid.uuid4().hex[:12]}",
                "reference": order_id,
                "amount": 30000000,
                "status": "success",
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(
        settings.paystack_secret_key.encode("utf-8"), body, hashlib.sha512
    ).hexdigest()

    webhook_resp = await client.post(
        "/api/v1/payments/webhook",
        content=body,
        headers={"x-paystack-signature": signature, "Content-Type": "application/json"},
    )
    assert webhook_resp.status_code == 200
    assert len(job_queue) == 1

    worker = SimpleWorker([job_queue], connection=sync_redis)
    worker.work(burst=True)

    assert f"Payment received for order {order_id}" in caplog.text
    assert buyer.email in caplog.text
