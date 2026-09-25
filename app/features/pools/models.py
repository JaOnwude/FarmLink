"""
Pools feature - SQLAlchemy ORM models.

pools (product, price, total_qty, available_qty, status) - index(status)

This is the row every order locks with SELECT ... FOR UPDATE - it's the
single source of truth for what's left to sell. available_qty is the
number that must never go below zero; total_qty is fixed at pool creation
(or grows as contributions come in - see contributions feature) and is
kept only for reporting/audit, never used in the availability check.
"""
import enum

from sqlalchemy import Enum, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class PoolStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class Pool(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "pools"

    product: Mapped[str] = mapped_column(String(255), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    # Free-text rather than a fixed enum - a co-op's real unit vocabulary
    # (bag, basket, mudu, tuber, crate, kg) is broader than any hardcoded
    # list would cover, and differs by product. Set once by the admin at
    # pool creation; every contribution and order against this pool is
    # implicitly in this unit. server_default backfills any pool created
    # before this column existed.
    unit: Mapped[str] = mapped_column(String(50), nullable=False, server_default="unit")
    total_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[PoolStatus] = mapped_column(
        Enum(PoolStatus, name="pool_status"), nullable=False, default=PoolStatus.OPEN, index=True
    )

    contributions: Mapped[list["Contribution"]] = relationship(back_populates="pool")
    orders: Mapped[list["Order"]] = relationship(back_populates="pool")
    allocations: Mapped[list["Allocation"]] = relationship(back_populates="pool")
    payouts: Mapped[list["Payout"]] = relationship(back_populates="pool")
