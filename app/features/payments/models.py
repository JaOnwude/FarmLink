"""
Payments feature - SQLAlchemy ORM models.

payments (order, amount, recorded_by, recorded_at)
processed_events (event_id UNIQUE, reference, processed_at) - for the webhook

processed_events is what makes the Paystack webhook idempotent: the
mock_payment_provider.py test script replays the same event_id to prove a
retried webhook delivery only confirms a payment once. Every event Paystack
sends is inserted here (event_id UNIQUE) before any business effect runs;
a duplicate insert is caught and the handler returns 200 without redoing
the work.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class Payment(UUIDPKMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_payments_order_id"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    # Who/what confirmed this payment - the webhook handler (system) for the
    # normal Paystack flow, or an admin user_id for a manual reconciliation.
    # Nullable because the automated webhook path has no human to attribute.
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    order: Mapped["Order"] = relationship(back_populates="payment")


class ProcessedEvent(UUIDPKMixin, TimestampMixin, Base):
    """created_at (from TimestampMixin) serves as processed_at here."""

    __tablename__ = "processed_events"

    event_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    reference: Mapped[str] = mapped_column(String(255), nullable=False)
