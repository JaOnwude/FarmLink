"""
Feed feature - HTTP layer only.

Scope: Firestore-backed SSE stream of pool activity
Build target: Day 11

Keep this file thin: request/response wiring and status codes only.
Business logic belongs in service.py, not here.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/feed", tags=["feed"])

# TODO (Day 11): add endpoints here
