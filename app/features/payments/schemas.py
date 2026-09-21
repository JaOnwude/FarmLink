"""
Payments feature - Pydantic request/response models.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class PaymentInitializeRequest(BaseModel):
    order_id: uuid.UUID


class PaymentInitializeResponse(BaseModel):
    authorization_url: str
    access_code: str
    reference: str


class PaymentOut(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    amount: Decimal
    recorded_at: datetime

    model_config = {"from_attributes": True}
