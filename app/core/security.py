"""
Cross-cutting auth utilities: password hashing, JWT issue/verify, and the
FastAPI dependencies every protected route in every feature depends on
(get_current_user, require_role). Lives in core/ rather than
features/auth/ because it's used well beyond the auth feature itself -
see CONTRIBUTING.md's core/ vs features/ rule.

This module answers two questions, in order, for every protected route:
  1. Is the caller who they claim to be? (valid JWT?) -> get_current_user,
     returns 401 if not.
  2. Is that caller allowed to do THIS? (right role, approved account?)
     -> require_role, returns 403 if not.
Ownership checks that go one level deeper than role (e.g. "is this
buyer's OWN order") are handled separately in each feature's own
service.py, since only that feature knows what "owns" means for its data.
"""
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.features.auth.models import User, UserRole

settings = get_settings()

# bcrypt has a hard 72-byte input limit - not a passlib quirk, bcrypt
# itself. Reject longer passwords with a clear error rather than silently
# truncating (silent truncation means two different long passwords could
# hash identically - a real, if obscure, security bug).
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > _BCRYPT_MAX_BYTES:
        raise ValueError(f"Password must be at most {_BCRYPT_MAX_BYTES} bytes.")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    raw = password.encode("utf-8")
    if len(raw) > _BCRYPT_MAX_BYTES:
        return False
    return bcrypt.checkpw(raw, password_hash.encode("utf-8"))


def create_access_token(user_id: uuid.UUID, role: UserRole) -> str:
    """sub = user id (string), role embedded so downstream checks don't
    need a DB round-trip just to know the role - get_current_user still
    hits the DB, but middleware/logging that only needs the role can read
    it straight from a decoded token."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user_id), "role": role.value, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Raises jose.JWTError on anything wrong - expired, bad signature,
    malformed. Callers turn that into a 401, never a 500."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


# tokenUrl is where Swagger UI's "Authorize" button will POST to try a login.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Decodes and validates the bearer token, then loads the matching
    user from the database. Any failure here - missing token, bad
    signature, expired token, or a user that's since been deleted from
    the database - collapses to the exact same 401 response, so an
    unauthenticated caller can never tell which specific check failed."""
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_error
    except JWTError:
        raise credentials_error

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_error
    return user


def require_role(*allowed_roles: UserRole):
    """Factory for a FastAPI dependency that restricts a route to specific
    roles. Usage per-endpoint:
        @router.post("/pools")
        async def create_pool(user: User = Depends(require_role(UserRole.ADMIN))):
    Ownership checks (e.g. 'is this buyer's own order') are one level more
    specific than role and stay in each feature's service.py, not here -
    this dependency only knows about roles, not which specific row is
    being touched."""

    async def _dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {', '.join(r.value for r in allowed_roles)}",
            )
        if not user.approved and user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account pending admin approval",
            )
        return user

    return _dependency
