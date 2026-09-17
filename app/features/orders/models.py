"""
Orders feature - SQLAlchemy ORM models.

orders (pool, buyer, qty, total, status, created_at) - index(status, created_at)
allocations (order, pool, qty)

Both live here, not split across two feature folders, because they're
written in the exact same transaction (see the brief's "hard problem"
diagram: UPDATE pool.available_qty -= quantity . INSERT order and
allocation, same COMMIT). Splitting them would mean importing across
feature boundaries for no benefit - allocations has no purpose outside
the order-placement flow.

The (status, created_at) composite index exists specifically for the
Day 9 sweep job's query: "find PENDING orders older than 24h".
"""
import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Index, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class OrderStatus(str, enum.Enum):
    PENDING = "pending"    # created, awaiting payment (paid within 24h or swept)
    PAID = "paid"
    EXPIRED = "expired"    # released by the 24h sweep
    CANCELLED = "cancelled"


class Order(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_status_created_at", "status", "created_at"),
    )

    pool_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("pools.id"), nullable=False
    )
    buyer_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    total: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status"), nullable=False, default=OrderStatus.PENDING
    )

    pool: Mapped["Pool"] = relationship(back_populates="orders")
    buyer: Mapped["User"] = relationship(back_populates="orders")
    allocations: Mapped[list["Allocation"]] = relationship(back_populates="order")
    payment: Mapped["Payment | None"] = relationship(back_populates="order", uselist=False)


class Allocation(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "allocations"

    order_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    pool_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("pools.id"), nullable=False
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped["Order"] = relationship(back_populates="allocations")
    pool: Mapped["Pool"] = relationship(back_populates="allocations")
