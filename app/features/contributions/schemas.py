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
