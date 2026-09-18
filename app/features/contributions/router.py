"""
Contributions feature - HTTP layer only.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role
from app.features.auth.models import User, UserRole
from app.features.contributions import service
from app.features.contributions.models import Contribution
from app.features.contributions.schemas import ContributionCreate, ContributionOut

router = APIRouter(prefix="/contributions", tags=["contributions"])


@router.post("", response_model=ContributionOut, status_code=status.HTTP_201_CREATED)
async def create_contribution(
    data: ContributionCreate,
    db: AsyncSession = Depends(get_db),
    farmer: User = Depends(require_role(UserRole.FARMER)),
) -> Contribution:
    try:
        return await service.create_contribution(db, farmer.id, data.pool_id, data.qty)
    except service.PoolNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pool not found")
    except service.PoolClosed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Pool is closed to new contributions"
        )


@router.get("/pool/{pool_id}", response_model=list[ContributionOut])
async def list_contributions_for_pool(
    pool_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[Contribution]:
    return await service.list_contributions_for_pool(db, pool_id)
