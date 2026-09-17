"""
Auth feature - SQLAlchemy ORM models.

users (email UNIQUE, password_hash, role, approved)
"""
import enum

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class UserRole(str, enum.Enum):
    FARMER = "farmer"
    BUYER = "buyer"
    ADMIN = "admin"


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"), nullable=False)

    # Farmers/buyers can be gated behind admin approval before they can
    # contribute stock or place orders - the "Role and ownership permit
    # this?" step in the flowchart checks this alongside role.
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    contributions: Mapped[list["Contribution"]] = relationship(back_populates="farmer")
    orders: Mapped[list["Order"]] = relationship(back_populates="buyer")
    payouts: Mapped[list["Payout"]] = relationship(back_populates="farmer")
