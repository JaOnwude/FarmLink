"""
Orders feature - HTTP layer only.
"""
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role
from app.features.auth.models import User, UserRole
from app.features.orders import service
from app.features.orders.models import Order
from app.features.orders.schemas import OrderCreate, OrderOut

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
async def create_order(
    data: OrderCreate,
    db: AsyncSession = Depends(get_db),
    buyer: User = Depends(require_role(UserRole.BUYER)),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Order:
    try:
        return await service.create_order(db, buyer.id, data.pool_id, data.qty, idempotency_key)
    except service.PoolNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pool not found")
    except service.PoolClosed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Pool is closed to new orders"
        )
    except service.InsufficientStock:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Not enough stock available in this pool"
        )


@router.get("/me", response_model=list[OrderOut])
async def list_my_orders(
    db: AsyncSession = Depends(get_db),
    buyer: User = Depends(require_role(UserRole.BUYER)),
) -> list[Order]:
    return await service.list_orders_for_buyer(db, buyer.id)


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.BUYER, UserRole.ADMIN)),
) -> Order:
    try:
        order = await service.get_order(db, order_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    # Ownership check: a buyer can see their own orders; an admin can see
    # any. This is the "and ownership" half of the flowchart's "Role and
    # ownership permit this?" step - require_role only checked role.
    if user.role == UserRole.BUYER and order.buyer_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your order")
    return order
