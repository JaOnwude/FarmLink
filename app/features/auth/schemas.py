"""
Auth feature - Pydantic request/response models.
"""
import uuid

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.features.auth.models import UserRole


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    role: UserRole

    @field_validator("role")
    @classmethod
    def no_self_serve_admin(cls, v: UserRole) -> UserRole:
        # Admins are promoted by an existing admin, not created through
        # open registration - closing this off is the "Role and ownership
        # permit this?" check applied at signup time, before any token
        # even exists.
        if v == UserRole.ADMIN:
            raise ValueError("Cannot self-register as admin.")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    role: UserRole
    approved: bool

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserRoleUpdate(BaseModel):
    role: UserRole