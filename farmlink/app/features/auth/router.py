"""
Auth feature - HTTP layer only.

Scope: Login, JWT issuance/verification, role+ownership guards
Build target: Day 3

Keep this file thin: request/response wiring and status codes only.
Business logic belongs in service.py, not here.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])

# TODO (Day 3): add endpoints here
