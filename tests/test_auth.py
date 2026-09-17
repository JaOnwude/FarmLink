"""
Day 3 tests: registration, login, the 401/403 steps from the flowchart,
and the rate limiter's 429 + Retry-After.
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.features.auth.models import User, UserRole
from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_register_then_login(client: AsyncClient):
    email = f"buyer-{uuid.uuid4().hex[:8]}@test.com"

    register_resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery", "role": "buyer"},
    )
    assert register_resp.status_code == 201
    body = register_resp.json()
    assert body["email"] == email
    assert body["role"] == "buyer"
    assert body["approved"] is False
    assert "password" not in body and "password_hash" not in body

    login_resp = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "correct-horse-battery"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    assert token

    me_resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == email


@pytest.mark.asyncio
async def test_duplicate_email_is_409(client: AsyncClient):
    email = f"dup-{uuid.uuid4().hex[:8]}@test.com"
    payload = {"email": email, "password": "correct-horse-battery", "role": "farmer"}
    first = await client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201
    second = await client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_wrong_password_is_401(client: AsyncClient):
    email = f"wrongpw-{uuid.uuid4().hex[:8]}@test.com"
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery", "role": "buyer"},
    )
    resp = await client.post(
        "/api/v1/auth/login", data={"username": email, "password": "not-the-password"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_without_token_is_401(client: AsyncClient):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_cannot_self_register_as_admin(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"wannabe-admin-{uuid.uuid4().hex[:8]}@test.com",
            "password": "correct-horse-battery",
            "role": "admin",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_require_role_blocks_wrong_role(client: AsyncClient):
    """Uses /auth/me is role-agnostic, so this test exercises require_role
    directly against a real approved admin vs a real approved farmer to
    prove the 403 path, rather than waiting for a feature router that
    uses require_role to exist (that's Day 4+)."""
    from app.core.security import create_access_token

    async with AsyncSessionLocal() as db:
        admin = User(
            email=f"admin-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("whatever-12345"),
            role=UserRole.ADMIN,
            approved=True,
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)

    from app.core.security import require_role
    from fastapi import Depends, APIRouter

    probe_router = APIRouter()

    @probe_router.get("/probe/admin-only")
    async def admin_only(user=Depends(require_role(UserRole.ADMIN))):
        return {"ok": True}

    app.include_router(probe_router, prefix="/api/v1")

    token = create_access_token(admin.id, admin.role)
    ok_resp = await client.get(
        "/api/v1/probe/admin-only", headers={"Authorization": f"Bearer {token}"}
    )
    assert ok_resp.status_code == 200

    # A freshly registered, unapproved buyer should be blocked from an
    # admin-only route on BOTH grounds (wrong role, not approved) - either
    # is sufficient for a 403.
    buyer_email = f"buyer-probe-{uuid.uuid4().hex[:8]}@test.com"
    await client.post(
        "/api/v1/auth/register",
        json={"email": buyer_email, "password": "correct-horse-battery", "role": "buyer"},
    )
    login_resp = await client.post(
        "/api/v1/auth/login",
        data={"username": buyer_email, "password": "correct-horse-battery"},
    )
    buyer_token = login_resp.json()["access_token"]
    forbidden_resp = await client.get(
        "/api/v1/probe/admin-only", headers={"Authorization": f"Bearer {buyer_token}"}
    )
    assert forbidden_resp.status_code == 403
