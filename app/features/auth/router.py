"""
Auth feature - HTTP layer only.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import create_access_token, get_current_user, require_role
from app.features.auth import service
from app.features.auth.models import User, UserRole
from app.features.auth.schemas import Token, UserOut, UserRegister, UserRoleUpdate

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(data: UserRegister, db: AsyncSession = Depends(get_db)) -> User:
    try:
        return await service.register_user(db, data)
    except service.EmailAlreadyRegistered:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )


@router.post("/login", response_model=Token)
async def login(
    # OAuth2PasswordRequestForm (not our own UserLogin schema) so this
    # endpoint matches what Swagger UI's "Authorize" button - and any
    # standard OAuth2 client - sends by default: form-encoded
    # username/password, where "username" carries the email.
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> Token:
    try:
        user = await service.authenticate_user(db, form_data.username, form_data.password)
    except service.InvalidCredentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(user.id, user.role)
    return Token(access_token=token)


@router.get("/me", response_model=UserOut)
async def read_current_user(user: User = Depends(get_current_user)) -> User:
    return user


@router.get("/pending", response_model=list[UserOut])
async def list_pending_users(
    role: UserRole | None = None,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
) -> list[User]:
    return await service.get_pending_users(db, role)


@router.patch("/{user_id}/approve", response_model=UserOut)
async def approve_user(
    # Two path segments (/{user_id}/approve), not a bare /{user_id} -
    # deliberately, so this can never collide with the static routes
    # above (/me, /pending) regardless of the order routes are
    # registered in. FastAPI matches routes in registration order, and a
    # single-segment /{user_id} would shadow those if it ever ended up
    # registered first.
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
) -> User:
    try:
        return await service.approve_user(db, user_id)
    except service.UserNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


@router.patch("/{user_id}/role", response_model=UserOut)
async def set_user_role(
    user_id: uuid.UUID,
    data: UserRoleUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role(UserRole.ADMIN)),
) -> User:
    if user_id == admin.id and data.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot demote yourself"
        )
    try:
        return await service.set_user_role(db, user_id, data.role)
    except service.UserNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")