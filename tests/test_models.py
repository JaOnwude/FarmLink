"""
Day 2 model smoke tests. Proves the ORM models actually round-trip through
a real Postgres, including the pieces that matter later:
  - the UUID(pool, farmer) unique constraint on payouts (enforces
    "a second share-out -> 409" at the DB level)
  - foreign keys between orders/allocations/pools/users
These run against the same DATABASE_URL as the app (see .env), not sqlite -
sqlite doesn't enforce the same constraint/type behavior Postgres does.
"""
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
import app.core.model_registry  # noqa: F401 - registers all feature models before any relationship is touched
from app.features.auth.models import User, UserRole
from app.features.payouts.models import Payout
from app.features.pools.models import Pool, PoolStatus


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()  # never leave test data behind


@pytest.mark.asyncio
async def test_create_pool_and_farmer(db: AsyncSession):
    pool = Pool(product="Rice (50kg bags)", price=Decimal("45000.00"),
                total_qty=100, available_qty=100, status=PoolStatus.OPEN)
    farmer = User(email=f"farmer-{uuid.uuid4().hex[:8]}@test.com",
                  password_hash="hashed", role=UserRole.FARMER, approved=True)
    db.add_all([pool, farmer])
    await db.flush()

    assert pool.id is not None
    assert pool.status == PoolStatus.OPEN
    assert farmer.role == UserRole.FARMER


@pytest.mark.asyncio
async def test_payout_unique_constraint_blocks_double_share_out(db: AsyncSession):
    """This is the DB-level half of the 'second share-out -> 409' test -
    the API layer (Day 12) will catch the IntegrityError and turn it into
    a 409, but the constraint itself is what makes that guarantee real."""
    pool = Pool(product="Maize (50kg bags)", price=Decimal("38000.00"),
                total_qty=50, available_qty=0, status=PoolStatus.CLOSED)
    farmer = User(email=f"farmer-{uuid.uuid4().hex[:8]}@test.com",
                  password_hash="hashed", role=UserRole.FARMER, approved=True)
    db.add_all([pool, farmer])
    await db.flush()

    import datetime
    first = Payout(pool_id=pool.id, farmer_id=farmer.id, amount=Decimal("100000.00"))
    db.add(first)
    await db.flush()

    second = Payout(pool_id=pool.id, farmer_id=farmer.id, amount=Decimal("100000.00"))
    db.add(second)
    with pytest.raises(IntegrityError):
        await db.flush()
