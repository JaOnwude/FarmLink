"""
Auth feature - business logic, called by router.py.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.features.auth.models import User, UserRole
from app.features.auth.schemas import UserRegister


class EmailAlreadyRegistered(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class UserNotFound(Exception):
    pass


async def register_user(db: AsyncSession, data: UserRegister) -> User:
    # Admins approve farmers/buyers before they can act (contribute stock,
    # place orders) - see User.approved. Buyers could reasonably be
    # auto-approved since they're not putting stock into the system, but
    # keeping everyone gated is the simpler, safer default for a co-op
    # that wants to know who's actually using it before real money moves.
    user = User(
        email=data.email,
        password_hash=hash_password(data.password),
        role=data.role,
        approved=False,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise EmailAlreadyRegistered(data.email)
    await db.commit()
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    # Same generic failure whether the email doesn't exist or the password
    # is wrong - confirming "that email isn't registered" to an anonymous
    # caller is its own small information leak.
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentials()
    return user


async def get_pending_users(db: AsyncSession, role: UserRole | None = None) -> list[User]:
    """Accounts still waiting on admin approval, oldest first - what an
    admin's approval queue would page through. Optionally filtered by
    role, since an admin reviewing farmers may not care about pending
    buyers in the same view."""
    stmt = select(User).where(User.approved.is_(False))
    if role is not None:
        stmt = stmt.where(User.role == role)
    stmt = stmt.order_by(User.created_at)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def approve_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Grants an account the approval flag that require_role() checks on
    every protected route. Takes effect immediately on the account's very
    next request - get_current_user always re-queries this row from the
    database rather than trusting anything baked into the caller's JWT,
    so there's no re-login or token refresh needed for this to kick in."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise UserNotFound(user_id)
    # Idempotent on purpose: approving an already-approved account isn't
    # an error, it just does nothing - an admin double-clicking "approve"
    # shouldn't get a 409 for it.
    user.approved = True
    await db.commit()
    await db.refresh(user)
    return user


async def set_user_role(db: AsyncSession, user_id: uuid.UUID, role: UserRole) -> User:
    """Promotes/changes an existing user's role. Only reachable by an
    existing admin (enforced at the router level) - this is how an org
    creates its second, third, etc. admin once the first one exists."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise UserNotFound(user_id)
    user.role = role
    await db.commit()
    await db.refresh(user)
    return user


async def bootstrap_admin(db: AsyncSession, email: str, password: str) -> None:
    """Idempotent - creates the org's very first admin from deployment-time
    env vars if they don't exist yet, or promotes/approves them if they'd
    already registered as a normal user. Deliberately never exposed over
    HTTP: controlled by whoever holds the environment's secrets, not by
    any API caller - the same reasoning UserRegister's no_self_serve_admin
    validator exists for."""
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            email=email,
            password_hash=hash_password(password),
            role=UserRole.ADMIN,
            approved=True,
        )
        db.add(user)
    else:
        user.role = UserRole.ADMIN
        user.approved = True
    await db.commit()