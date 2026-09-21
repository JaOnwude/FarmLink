"""
Orders feature - background jobs, run by the worker process (Day 10),
not inline with the request that triggers them.

Plain synchronous functions - RQ workers are synchronous processes, not
asyncio. Take only plain data as arguments (strings, numbers), never ORM
objects or a DB session: RQ serializes arguments to store them in Redis
until a worker is free to run them, and neither survives that trip.
"""
import logging

logger = logging.getLogger("farmlink.jobs.orders")


def send_order_confirmation_email(buyer_email: str, order_id: str, qty: int, total: str) -> None:
    """Stub - no real email provider (SendGrid, SES, Postmark) is wired
    up yet. Deliberately its own function so plugging one in later is a
    one-function change, not a hunt through every place that enqueues
    this job."""
    logger.info(
        f"[EMAIL] To: {buyer_email} | Order {order_id} confirmed: {qty} unit(s), total {total}"
    )


def notify_admin_of_new_order(order_id: str, pool_id: str, buyer_email: str, qty: int) -> None:
    logger.info(f"[ADMIN NOTIFY] New order {order_id} on pool {pool_id} by {buyer_email}: qty {qty}")
