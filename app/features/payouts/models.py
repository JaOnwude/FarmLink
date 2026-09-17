"""
Payouts feature - SQLAlchemy ORM models.

payouts (pool, farmer, amount, at) - UNIQUE(pool, farmer)

The UNIQUE(pool, farmer) constraint is what makes the "second share-out ->
409" test (from the five must-pass tests) enforceable at the database
level, not just in application logic - a second payout attempt for the
same farmer/pool hits a constraint violation the service layer catches
and turns into a 409, rather than relying on an application-level check
that a race condition could slip past.
"""
import uuid

from sqlalchemy import ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class Payout(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "payouts"
    __table_args__ = (
        UniqueConstraint("pool_id", "farmer_id", name="uq_payouts_pool_farmer"),
    )

    pool_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("pools.id"), nullable=False
    )
    farmer_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    pool: Mapped["Pool"] = relationship(back_populates="payouts")
    farmer: Mapped["User"] = relationship(back_populates="payouts")
