"""
Contributions feature - SQLAlchemy ORM models.

contributions (pool, farmer, qty, at) - index(pool)
"""
import uuid

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class Contribution(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "contributions"

    pool_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("pools.id"), nullable=False, index=True
    )
    farmer_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)

    pool: Mapped["Pool"] = relationship(back_populates="contributions")
    farmer: Mapped["User"] = relationship(back_populates="contributions")
