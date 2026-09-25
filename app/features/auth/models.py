"""
Auth feature - SQLAlchemy ORM models.

Defines the `users` table: one row per account, holding login
credentials, their role (farmer / buyer / admin), and an admin-approval
flag. Everything else in the app (pools, orders, contributions, payouts)
links back to a user via foreign key.
"""
import enum

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class UserRole(str, enum.Enum):
    """The three account types in the system. Stored as a native Postgres
    enum (see the `Enum(...)` column below) so the database itself
    rejects any value outside this set, not just the application code."""
    FARMER = "farmer"
    BUYER = "buyer"
    ADMIN = "admin"


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # UUIDPKMixin gives this table a UUID primary key (`id`); TimestampMixin
    # gives it `created_at`/`updated_at` - both defined once and reused
    # across every model in the app instead of repeating them per table.
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"), nullable=False)

    # Farmers and buyers can be gated behind admin approval before they're
    # allowed to contribute stock or place orders (a fresh signup starts
    # as unapproved). Route-level permission checks look at this flag
    # together with `role` - having the right role alone isn't enough if
    # the account hasn't been approved yet.
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Reverse relationships: lets code do `user.orders`, `user.payouts`
    # etc. without writing a manual join each time. `back_populates` keeps
    # both sides (this model and Contribution/Order/Payout) in sync.
    contributions: Mapped[list["Contribution"]] = relationship(back_populates="farmer")
    orders: Mapped[list["Order"]] = relationship(back_populates="buyer")
    payouts: Mapped[list["Payout"]] = relationship(back_populates="farmer")
