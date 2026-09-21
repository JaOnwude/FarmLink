"""
Contributions feature - Pydantic request/response models.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ContributionCreate(BaseModel):
    pool_id: uuid.UUID
    qty: int = Field(gt=0)


class ContributionOut(BaseModel):
    id: uuid.UUID
    pool_id: uuid.UUID
    farmer_id: uuid.UUID
    qty: int
    created_at: datetime

    model_config = {"from_attributes": True}


class PoolContributorSummary(BaseModel):
    """One row per farmer who has contributed to a pool - their TOTAL
    across every contribution they've made, not one row per contribution.
    farmer_id is the same account id assigned at registration
    (users.id) - there's no separate "contributor ID"."""

    farmer_id: uuid.UUID
    farmer_email: str
    total_qty: int
