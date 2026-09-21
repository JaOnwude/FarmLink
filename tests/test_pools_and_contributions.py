"""
Day 4 tests: pool creation/listing (admin-only writes, public reads), and
contributions including the brief's required "closed pool rejects
contributions with 409" test.
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token, hash_password
from app.features.auth.models import User, UserRole
from app.features.pools.models import Pool, PoolStatus
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
async def test_admin_can_create_pool(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    resp = await client.post(
        "/api/v1/pools",
        json={"product": "Rice (50kg bags)", "price": "45000.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["product"] == "Rice (50kg bags)"
    assert body["status"] == "open"
    assert body["total_qty"] == 0
    assert body["available_qty"] == 0


@pytest.mark.asyncio
async def test_non_admin_cannot_create_pool(client: AsyncClient):
    farmer = await _make_user(UserRole.FARMER)
    resp = await client.post(
        "/api/v1/pools",
        json={"product": "Rice", "price": "45000.00", "unit": "bag"},
        headers=_auth_header(farmer),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_pool_without_token_is_401(client: AsyncClient):
    resp = await client.post("/api/v1/pools", json={"product": "Rice", "price": "45000.00", "unit": "bag"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_negative_price_is_422(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    resp = await client.post(
        "/api/v1/pools",
        json={"product": "Rice", "price": "-5.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_pool_listing_is_public(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    await client.post(
        "/api/v1/pools",
        json={"product": "Maize", "price": "30000.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    resp = await client.get("/api/v1/pools")  # no auth header at all
    assert resp.status_code == 200
    assert any(p["product"] == "Maize" for p in resp.json())


@pytest.mark.asyncio
async def test_farmer_contribution_increases_pool_quantities(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)

    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Beans", "price": "60000.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]

    contrib_resp = await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 20},
        headers=_auth_header(farmer),
    )
    assert contrib_resp.status_code == 201
    assert contrib_resp.json()["qty"] == 20

    pool_after = await client.get(f"/api/v1/pools/{pool_id}")
    assert pool_after.json()["total_qty"] == 20
    assert pool_after.json()["available_qty"] == 20

    # A second contribution accumulates rather than overwriting.
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 15},
        headers=_auth_header(farmer),
    )
    pool_final = await client.get(f"/api/v1/pools/{pool_id}")
    assert pool_final.json()["total_qty"] == 35
    assert pool_final.json()["available_qty"] == 35


@pytest.mark.asyncio
async def test_buyer_cannot_contribute(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    buyer = await _make_user(UserRole.BUYER)
    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Yam", "price": "10000.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]

    resp = await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 5},
        headers=_auth_header(buyer),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_closed_pool_rejects_contributions_with_409(client: AsyncClient):
    """One of the five must-pass tests from the brief."""
    admin = await _make_user(UserRole.ADMIN)
    farmer = await _make_user(UserRole.FARMER)

    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Cassava", "price": "8000.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]

    close_resp = await client.patch(
        f"/api/v1/pools/{pool_id}/close", headers=_auth_header(admin)
    )
    assert close_resp.status_code == 200
    assert close_resp.json()["status"] == "closed"

    contrib_resp = await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 10},
        headers=_auth_header(farmer),
    )
    assert contrib_resp.status_code == 409


@pytest.mark.asyncio
async def test_closing_already_closed_pool_is_409(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Sorghum", "price": "9000.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]

    first_close = await client.patch(f"/api/v1/pools/{pool_id}/close", headers=_auth_header(admin))
    assert first_close.status_code == 200

    second_close = await client.patch(f"/api/v1/pools/{pool_id}/close", headers=_auth_header(admin))
    assert second_close.status_code == 409


@pytest.mark.asyncio
async def test_pool_create_requires_unit(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    resp = await client.post(
        "/api/v1/pools",
        json={"product": "Rice", "price": "45000.00"},  # unit omitted
        headers=_auth_header(admin),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_pool_out_includes_unit(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    resp = await client.post(
        "/api/v1/pools",
        json={"product": "Yam", "price": "10000.00", "unit": "tuber"},
        headers=_auth_header(admin),
    )
    assert resp.status_code == 201
    assert resp.json()["unit"] == "tuber"


@pytest.mark.asyncio
async def test_contributor_summary_aggregates_per_farmer_not_per_contribution(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    farmer_a = await _make_user(UserRole.FARMER)
    farmer_b = await _make_user(UserRole.FARMER)

    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Maize", "price": "30000.00", "unit": "basket"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]

    # Farmer A contributes twice - the summary must show ONE row for them
    # with the SUM, not two separate rows.
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 10},
        headers=_auth_header(farmer_a),
    )
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 5},
        headers=_auth_header(farmer_a),
    )
    await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 8},
        headers=_auth_header(farmer_b),
    )

    resp = await client.get(f"/api/v1/contributions/pool/{pool_id}/summary")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2  # one row per farmer, not per contribution

    by_id = {row["farmer_id"]: row for row in rows}
    assert by_id[str(farmer_a.id)]["total_qty"] == 15  # 10 + 5, summed
    assert by_id[str(farmer_a.id)]["farmer_email"] == farmer_a.email
    assert by_id[str(farmer_b.id)]["total_qty"] == 8
    # farmer_id doubles as "contributor ID" - the same account id issued at registration
    assert by_id[str(farmer_a.id)]["farmer_id"] == str(farmer_a.id)


@pytest.mark.asyncio
async def test_contributor_summary_empty_pool_returns_empty_list(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Tomatoes", "price": "5000.00", "unit": "crate"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]

    resp = await client.get(f"/api/v1/contributions/pool/{pool_id}/summary")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_unapproved_farmer_cannot_contribute(client: AsyncClient):
    admin = await _make_user(UserRole.ADMIN)
    unapproved_farmer = await _make_user(UserRole.FARMER, approved=False)
    pool_resp = await client.post(
        "/api/v1/pools",
        json={"product": "Millet", "price": "7000.00", "unit": "bag"},
        headers=_auth_header(admin),
    )
    pool_id = pool_resp.json()["id"]

    resp = await client.post(
        "/api/v1/contributions",
        json={"pool_id": pool_id, "qty": 5},
        headers=_auth_header(unapproved_farmer),
    )
    assert resp.status_code == 403
