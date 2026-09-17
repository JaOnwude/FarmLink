"""
Import every feature's models module here, once, purely for the side
effect of registering each class with SQLAlchemy's mapper registry.

Why this file exists: models.py files use string relationship references
across feature boundaries (User.contributions -> "Contribution", defined
in a different feature's models.py). SQLAlchemy can only resolve those
strings if every referenced class has actually been imported into the
same registry by the time mappers configure - which happens automatically
on first use. Import THIS module (not individual feature models modules)
wherever the full ORM needs to work: app startup, Alembic, and any test
that touches more than one feature's models.
"""
from app.features.auth import models as _auth_models  # noqa: F401
from app.features.pools import models as _pools_models  # noqa: F401
from app.features.contributions import models as _contributions_models  # noqa: F401
from app.features.orders import models as _orders_models  # noqa: F401
from app.features.payments import models as _payments_models  # noqa: F401
from app.features.payouts import models as _payouts_models  # noqa: F401
