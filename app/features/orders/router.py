"""
Orders feature - HTTP layer only.

Translates HTTP requests into calls to `service.py` and turns the
service's domain exceptions (PoolNotFound, PoolClosed, InsufficientStock)
into the right HTTP status codes. No business logic lives here - if you're
looking for the actual order-placement rules, see orders/service.py.
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
    # Optional client-supplied key so a retried/duplicated HTTP request
    # (e.g. a mobile client that timed out and resent) doesn't create a
    # second order - service.create_order() uses this to return the
    # original order instead of creating a duplicate.
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Order:
    try:
        return await service.create_order(
            db, buyer.id, buyer.email, data.pool_id, data.qty, idempotency_key
        )
    # Each domain-level exception from the service layer maps to a
    # specific, client-friendly HTTP status - the service layer itself
    # knows nothing about HTTP status codes, only about order-placement
    # rules.
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
    # require_role() above only checks that the caller HAS an allowed
    # role (buyer or admin) - it says nothing about WHICH orders they're
    # allowed to see. This second check adds ownership on top: a plain
    # buyer may only view their own orders, while an admin can view any
    # order regardless of who placed it.
    if user.role == UserRole.BUYER and order.buyer_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your order")
    return order
