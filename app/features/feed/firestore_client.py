"""
Firestore client for the durable pool feed. Lives in features/feed/, not
core/, for the same reason payments/paystack_client.py lives in
features/payments/ - it's used by exactly one feature, so per
CONTRIBUTING.md's rule it belongs to that feature, not the shared layer.

Honest limitation of this sandbox: there are no real Google Cloud
credentials or network access to googleapis.com here, so this module's
actual write-to-Firestore path is unverified in this environment - the
same situation Paystack was in, except Paystack's HTTP boundary could
still be exercised against a fake response. Firestore's SDK talks
directly to Google's infrastructure with no equivalent local stand-in,
so what's verified here is: the code is structurally correct, it
gracefully no-ops when unconfigured (true in every environment this
project has run in so far), and the call site around it works. The
actual write needs verifying against a real Firestore project once
credentials exist - flagged, not silently assumed to work.

Gracefully no-ops (logs once, does nothing further) when
FIRESTORE_PROJECT_ID isn't set - true for local dev and this test suite,
neither of which has real credentials. Production sets
FIRESTORE_PROJECT_ID and FIRESTORE_CREDENTIALS_PATH and this becomes a
real, durable write.
"""
import logging
from datetime import datetime, timezone

from app.core.config import get_settings

logger = logging.getLogger("farmlink.firestore")
settings = get_settings()

_client = None
_warned_unconfigured = False


def _get_client():
    global _client, _warned_unconfigured

    if not settings.firestore_project_id:
        if not _warned_unconfigured:
            logger.warning(
                "Firestore not configured (FIRESTORE_PROJECT_ID unset) - "
                "pool feed events will be delivered live via SSE but not "
                "persisted to Firestore."
            )
            _warned_unconfigured = True
        return None

    if _client is None:
        from google.cloud import firestore

        if settings.firestore_credentials_path:
            _client = firestore.Client.from_service_account_json(
                settings.firestore_credentials_path, project=settings.firestore_project_id
            )
        else:
            _client = firestore.Client(project=settings.firestore_project_id)
    return _client


def write_pool_feed_event(pool_id: str, event_type: str, data: dict) -> None:
    """One document per event, under pools/{pool_id}/feed. Called from a
    background job (Day 10's queue), never inline with the request that
    triggers it - a slow or unreachable Firestore must never add latency
    to placing an order or a contribution."""
    client = _get_client()
    if client is None:
        return

    client.collection("pools").document(pool_id).collection("feed").add(
        {
            "event_type": event_type,
            "data": data,
            "created_at": datetime.now(timezone.utc),
        }
    )


def reset_client_for_testing() -> None:
    """Test-only: forces the next call to _get_client() to re-evaluate
    settings from scratch, so a test can monkeypatch
    settings.firestore_project_id and confirm the no-op path without a
    real client ever being constructed, and without state leaking in
    from whichever test ran first."""
    global _client, _warned_unconfigured
    _client = None
    _warned_unconfigured = False
