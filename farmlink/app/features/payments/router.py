"""
Payments feature - HTTP layer only.

Scope: Paystack transaction init + webhook confirmation
Build target: Day 8-9

Keep this file thin: request/response wiring and status codes only.
Business logic belongs in service.py, not here.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/payments", tags=["payments"])

# TODO (Day 8-9): add endpoints here
