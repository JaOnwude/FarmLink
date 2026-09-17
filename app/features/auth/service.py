"""
Auth feature - business logic, called by router.py.
"""
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
