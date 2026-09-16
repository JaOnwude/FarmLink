"""
Orders feature - HTTP layer only.

Scope: The pooled-stock concurrency problem: SELECT ... FOR UPDATE order flow
Build target: Day 5

Keep this file thin: request/response wiring and status codes only.
Business logic belongs in service.py, not here.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/orders", tags=["orders"])

# TODO (Day 5): add endpoints here
