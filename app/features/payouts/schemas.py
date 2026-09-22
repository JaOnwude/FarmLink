"""
Payouts feature - Pydantic request/response models.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class PayoutOut(BaseModel):
    id: uuid.UUID
    pool_id: uuid.UUID
    farmer_id: uuid.UUID
    amount: Decimal
    created_at: datetime

    model_config = {"from_attributes": True}
