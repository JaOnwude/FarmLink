"""
Orders feature - Pydantic request/response models.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.features.orders.models import OrderStatus


class OrderCreate(BaseModel):
    pool_id: uuid.UUID
    qty: int = Field(gt=0)


class OrderOut(BaseModel):
    id: uuid.UUID
    pool_id: uuid.UUID
    buyer_id: uuid.UUID
    qty: int
    total: Decimal
    status: OrderStatus
    created_at: datetime

    model_config = {"from_attributes": True}
