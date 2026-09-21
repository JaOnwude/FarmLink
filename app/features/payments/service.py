"""
Payments feature - business logic, called by router.py.

Two flows:
  1. initialize_payment: buyer starts a payment for a PENDING order we
     already created (Day 5). We call Paystack, hand back a URL/access
     code for the client to complete payment - nothing in OUR database
     changes yet. The order stays PENDING until...
  2. process_webhook_event: Paystack tells us, asynchronously, that the
     charge succeeded. THIS is what actually marks an order PAID - never
     the client-side redirect after payment, which is easy to spoof or
     which the buyer can simply not follow through on.

Idempotency is the load-bearing property of (2): Paystack retries
webhook delivery, so every event is recorded in processed_events
(event_id UNIQUE) in the SAME transaction as its business effect. If the
insert fails on the unique constraint, we know this exact event has
already been fully handled - rollback and return without redoing
anything. This is deliberately the same pattern this project's Day 5
order idempotency uses, and it's exactly what mock_payment_provider.py
(the brief's test script) is built to verify: a duplicate event changes
nothing, a forged signature never reaches this function at all, and an
event for a reference we don't recognize is recorded but has no effect.
"""
import json
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jobs import enqueue
from app.features.auth.models import User
from app.features.orders.models import Order, OrderStatus
from app.features.payments import paystack_client
from app.features.payments.models import Payment, ProcessedEvent
from app.features.payments.tasks import send_payment_confirmation_email


class OrderNotFound(Exception):
    pass


class OrderNotOwnedByBuyer(Exception):
    pass


class OrderNotPending(Exception):
    pass


class InvalidWebhookSignature(Exception):
    pass


async def initialize_payment(
    db: AsyncSession, buyer_id: uuid.UUID, buyer_email: str, order_id: uuid.UUID
) -> dict:
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if order is None:
        raise OrderNotFound(order_id)
    if order.buyer_id != buyer_id:
        raise OrderNotOwnedByBuyer(order_id)
    if order.status != OrderStatus.PENDING:
        raise OrderNotPending(order_id)

    # Paystack wants the smallest currency unit (kobo for NGN), never a
    # decimal naira amount - a Decimal total of 45000.00 becomes 4500000.
    amount_kobo = int((order.total * 100).to_integral_value())

    # order.id doubles as the Paystack reference - one less thing to
    # generate, store, or reconcile. The webhook handler below looks
    # orders up by this same id.
    data = await paystack_client.initialize_transaction(
        email=buyer_email, amount_kobo=amount_kobo, reference=str(order.id)
    )
    return data


async def process_webhook_event(db: AsyncSession, raw_body: bytes, signature: str) -> None:
    if not paystack_client.verify_webhook_signature(raw_body, signature):
        raise InvalidWebhookSignature()

    payload = json.loads(raw_body)
    event_type = payload.get("event")
    data = payload.get("data", {})
    reference = data.get("reference")
    # Paystack's transaction "id" is the most specific unique identifier
    # available; fall back to event+reference for payload shapes that
    # omit it (defensive - real Paystack payloads always include id).
    event_id = str(data.get("id") or f"{event_type}:{reference}")

    processed = ProcessedEvent(event_id=event_id, reference=reference or "")
    db.add(processed)
    try:
        await db.flush()
    except IntegrityError:
        # Same event_id already recorded - this exact webhook delivery
        # has already been fully handled (or is being handled
        # concurrently). Nothing more to do; NOT an error.
        await db.rollback()
        return

    if event_type != "charge.success":
        # Recorded for the audit trail, but no business effect - we only
        # act on successful charges.
        await db.commit()
        return

    if reference is None:
        await db.commit()
        return

    try:
        order_id = uuid.UUID(reference)
    except ValueError:
        # Reference isn't one of our order ids at all - orphan event.
        # Recorded above, no further action.
        await db.commit()
        return

    result = await db.execute(select(Order).where(Order.id == order_id).with_for_update())
    # Locked, not a plain SELECT: closes the race with Day 9's sweep job.
    # Without this lock, a payment succeeding at the exact moment the
    # sweep decides this same order is expired could interleave with the
    # sweep's own lock+check - whichever locks the order row first now
    # wins outright, the other correctly sees the already-updated status.
    order = result.scalar_one_or_none()
    if order is None or order.status == OrderStatus.PAID:
        # Unknown reference, or already paid (a second success event for
        # the same order shouldn't happen once processed_events is doing
        # its job, but this is a cheap extra guard). Either way: no
        # further action, event stays recorded.
        await db.commit()
        return

    order.status = OrderStatus.PAID
    payment = Payment(
        order_id=order.id,
        amount=Decimal(data.get("amount", 0)) / 100,
        recorded_by=None,  # automated webhook path, not a human admin
    )
    db.add(payment)
    await db.commit()

    # Async SQLAlchemy doesn't do implicit lazy-loading (order.buyer would
    # raise MissingGreenlet outside an explicit await), so a plain query
    # for the email is simpler than eager-loading the relationship just
    # for this one background-job argument.
    buyer_result = await db.execute(select(User.email).where(User.id == order.buyer_id))
    buyer_email = buyer_result.scalar_one_or_none()
    if buyer_email:
        enqueue(send_payment_confirmation_email, buyer_email, str(order.id), str(payment.amount))
