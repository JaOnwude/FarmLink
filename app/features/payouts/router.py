"""
Payouts feature - HTTP layer only.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role
from app.features.auth.models import User, UserRole
from app.features.payouts import service
from app.features.payouts.models import Payout
from app.features.payouts.schemas import PayoutOut

router = APIRouter(prefix="/payouts", tags=["payouts"])


@router.post("/pools/{pool_id}/distribute", response_model=list[PayoutOut])
async def distribute_payouts(
    pool_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
) -> list[Payout]:
    try:
        return await service.calculate_and_create_payouts(db, pool_id)
    except service.PoolNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pool not found")
    except service.PoolNotClosed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pool must be closed before it can be paid out",
        )
    except service.PoolAlreadyPaidOut:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This pool has already been paid out"
        )


@router.get("/pools/{pool_id}", response_model=list[PayoutOut])
async def list_payouts_for_pool(
    pool_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[Payout]:
    return await service.list_payouts_for_pool(db, pool_id)


@router.get("/me", response_model=list[PayoutOut])
async def list_my_payouts(
    db: AsyncSession = Depends(get_db),
    farmer: User = Depends(require_role(UserRole.FARMER)),
) -> list[Payout]:
    return await service.list_payouts_for_farmer(db, farmer.id)
