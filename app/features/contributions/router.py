"""
Contributions feature - HTTP layer only.

Scope: Farmers adding stock quantity to a pool
Build target: Day 4

Keep this file thin: request/response wiring and status codes only.
Business logic belongs in service.py, not here.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/contributions", tags=["contributions"])

# TODO (Day 4): add endpoints here
