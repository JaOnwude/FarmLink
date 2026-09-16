"""
Payouts feature - HTTP layer only.

Scope: Farmer payout calculation once a pool closes
Build target: Day 12

Keep this file thin: request/response wiring and status codes only.
Business logic belongs in service.py, not here.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/payouts", tags=["payouts"])

# TODO (Day 12): add endpoints here
