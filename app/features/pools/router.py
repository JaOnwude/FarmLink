"""
Pools feature - HTTP layer only.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role
from app.features.auth.models import User, UserRole
from app.features.pools import service
from app.features.pools.models import Pool, PoolStatus
from app.features.pools.schemas import PoolCreate, PoolOut

router = APIRouter(prefix="/pools", tags=["pools"])


@router.post("", response_model=PoolOut, status_code=status.HTTP_201_CREATED)
async def create_pool(
    data: PoolCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
) -> Pool:
    return await service.create_pool(db, data)


@router.get("", response_model=list[PoolOut])
async def list_pools(
    status_filter: PoolStatus | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
) -> list[PoolOut]:
    # Deliberately public (no auth dependency) - browsing what's available
    # to pool into is how a buyer or farmer decides whether to sign up at
    # all. Everything that changes state (create/close/contribute/order)
    # stays behind auth.
    return await service.list_pools(db, status_filter)


@router.get("/{pool_id}", response_model=PoolOut)
async def get_pool(pool_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> PoolOut:
    try:
        return await service.get_pool(db, pool_id)
    except service.PoolNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pool not found")


@router.patch("/{pool_id}/close", response_model=PoolOut)
async def close_pool(
    pool_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
) -> Pool:
    try:
        return await service.close_pool(db, pool_id)
    except service.PoolNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pool not found")
    except service.PoolAlreadyClosed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Pool already closed")
