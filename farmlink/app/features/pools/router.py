"""
Pools feature - HTTP layer only.

Scope: Admin-created stock pools buyers order from
Build target: Day 4

Keep this file thin: request/response wiring and status codes only.
Business logic belongs in service.py, not here.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/pools", tags=["pools"])

# TODO (Day 4): add endpoints here
