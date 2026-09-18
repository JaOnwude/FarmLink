"""
Pools feature - Pydantic request/response models.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.features.pools.models import PoolStatus


class PoolCreate(BaseModel):
    product: str = Field(min_length=1, max_length=255)
    price: Decimal = Field(gt=0, decimal_places=2)
    # total_qty/available_qty are NOT set here - a pool starts empty and
    # grows as farmers contribute (see contributions feature). Admin
    # declares what's being pooled and at what price; the co-op fills it.


class PoolOut(BaseModel):
    id: uuid.UUID
    product: str
    price: Decimal
    total_qty: int
    available_qty: int
    status: PoolStatus
    created_at: datetime

    model_config = {"from_attributes": True}
