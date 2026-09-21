"""
Payments feature - HTTP layer only.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role
from app.features.auth.models import User, UserRole
from app.features.payments import service
from app.features.payments.schemas import PaymentInitializeRequest, PaymentInitializeResponse

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/initialize", response_model=PaymentInitializeResponse)
async def initialize_payment(
    data: PaymentInitializeRequest,
    db: AsyncSession = Depends(get_db),
    buyer: User = Depends(require_role(UserRole.BUYER)),
) -> PaymentInitializeResponse:
    try:
        paystack_data = await service.initialize_payment(db, buyer.id, buyer.email, data.order_id)
    except service.OrderNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    except service.OrderNotOwnedByBuyer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your order")
    except service.OrderNotPending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Order is not awaiting payment (already paid, expired, or cancelled)",
        )
    return PaymentInitializeResponse(
        authorization_url=paystack_data["authorization_url"],
        access_code=paystack_data["access_code"],
        reference=paystack_data["reference"],
    )


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def paystack_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_paystack_signature: str | None = Header(default=None, alias="x-paystack-signature"),
) -> dict:
    # Read the RAW body - not a parsed model. The signature Paystack sends
    # is an HMAC over the exact bytes it transmitted; re-serializing a
    # parsed Pydantic model back to JSON is not guaranteed to produce
    # byte-identical output (key order, spacing), which would make a
    # genuinely valid signature fail verification.
    raw_body = await request.body()

    if x_paystack_signature is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing signature")

    try:
        await service.process_webhook_event(db, raw_body, x_paystack_signature)
    except service.InvalidWebhookSignature:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    # Always 200 for anything past signature verification - including
    # duplicates and orphan references - so Paystack doesn't interpret a
    # deliberately-ignored event as a delivery failure and keep retrying it.
    return {"received": True}
