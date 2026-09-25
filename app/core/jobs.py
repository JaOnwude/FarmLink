"""
RQ job queue setup - the real background-task mechanism used by orders, payments, and the feed.

Why a real queue instead of FastAPI's BackgroundTasks: BackgroundTasks
runs inside the SAME process that served the request. If that process
restarts (a deploy, a crash, a pod eviction) before the task finishes,
the task is just gone - no record it was ever supposed to happen, no
retry. A job enqueued to Redis survives that: it sits in the queue until
a worker - a SEPARATE, always-running process (see the `worker` service
in docker-compose.yml) - picks it up, restart or not.

Why RQ over Celery: this project already runs Redis for cache and rate
limiting. RQ needs nothing else - Celery's simplest setup still wants a
broker (usually Redis anyway) plus config for a result backend. RQ is
the smaller addition for a project already this size.

RQ's Queue needs a SYNCHRONOUS redis-py client, not the async one the
rest of the app uses (app.core.redis_client) - workers are plain
synchronous Python processes, not asyncio.
"""
import redis
from rq import Queue

from app.core.config import get_settings

settings = get_settings()

sync_redis = redis.from_url(settings.redis_url)
job_queue = Queue("default", connection=sync_redis)


def enqueue(func, *args, **kwargs):
    """Thin wrapper so every call site goes through one place - makes it
    trivial to mock in tests (monkeypatch this function) or swap the
    queue implementation later without touching every feature that
    enqueues a job."""
    return job_queue.enqueue(func, *args, **kwargs)
