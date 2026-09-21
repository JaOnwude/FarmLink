"""
Thin wrapper around Paystack's REST API. Isolated in its own module,
deliberately, so tests mock `initialize_transaction` here rather than
mocking httpx generically - the rest of the payments feature never knows
or cares that Paystack is the specific provider behind this function.

Two functions only: starting a transaction (buyer-initiated) and
verifying a webhook signature (Paystack-initiated). Everything else -
what happens once a webhook arrives - is business logic and lives in
service.py, not here.
"""
import hashlib
import hmac

import httpx

from app.core.config import get_settings

settings = get_settings()

PAYSTACK_BASE_URL = "https://api.paystack.co"


class PaystackError(Exception):
    pass


async def initialize_transaction(
    email: str, amount_kobo: int, reference: str, callback_url: str | None = None
) -> dict:
    """POST /transaction/initialize. amount_kobo is the amount in the
    smallest currency unit (kobo for NGN) - Paystack never accepts a
    decimal naira amount. reference is OUR order id (str(order.id)) -
    reusing it rather than minting a separate Paystack-specific reference
    keeps the order <-> Paystack transaction link trivial to look up in
    both directions."""
    payload = {"email": email, "amount": amount_kobo, "reference": reference}
    if callback_url:
        payload["callback_url"] = callback_url

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{PAYSTACK_BASE_URL}/transaction/initialize",
            json=payload,
            headers={"Authorization": f"Bearer {settings.paystack_secret_key}"},
        )
    body = response.json()
    if response.status_code != 200 or not body.get("status"):
        raise PaystackError(body.get("message", "Paystack initialize_transaction failed"))
    return body["data"]


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """Paystack signs the raw request body with HMAC-SHA512 using the
    SECRET key (not the public key) and sends it as x-paystack-signature.
    Constant-time comparison (hmac.compare_digest) - a plain == would let
    an attacker infer the correct signature one byte at a time via
    response-time differences (a timing attack)."""
    expected = hmac.new(
        settings.paystack_secret_key.encode("utf-8"), raw_body, hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
