"""
Payments feature - background jobs, run by the worker process.
"""
import logging

logger = logging.getLogger("farmlink.jobs.payments")


def send_payment_confirmation_email(buyer_email: str, order_id: str, amount: str) -> None:
    logger.info(f"[EMAIL] To: {buyer_email} | Payment received for order {order_id}: {amount}")
