"""
Entry point for the background job worker process (Day 10). Runs as its
OWN container (see docker-compose.yml's `worker` service) - a separate
process from the API. This separation is the entire point: it's what
lets an enqueued job survive an API restart. The API container can
crash, redeploy, or scale to zero, and a job already sitting in Redis
is untouched - the worker container keeps pulling from the queue
independent of whatever the API is doing.

Run directly: python -m app.worker
"""
from rq import Worker

from app.core.jobs import job_queue, sync_redis

# Import task modules so RQ can resolve job functions by their dotted
# import path when it pulls a job off the queue - RQ stores "which
# function" as a string path, not the function object itself.
import app.features.orders.tasks  # noqa: F401
import app.features.payments.tasks  # noqa: F401

if __name__ == "__main__":
    worker = Worker([job_queue], connection=sync_redis)
    worker.work()
