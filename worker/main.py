import logging
import threading
import time

import redis
from rq import Worker

from app.config import settings
from worker.decay_job import run_decay_job
from worker.heartbeat import beat, make_client
from worker.queue import get_queue, get_redis_connection
from worker.reflection_agent import run_reflection_agent

logger = logging.getLogger(__name__)


def _heartbeat_loop() -> None:
    client = make_client()
    while True:
        try:
            beat(client)
        except redis.RedisError:
            time.sleep(1)
            client = make_client()
        time.sleep(settings.worker_heartbeat_interval_seconds)


def _maintenance_loop() -> None:
    """Periodic decay + reflection — never on the API request path."""
    decay_every = max(settings.decay_interval_seconds, 1.0)
    reflect_every = max(settings.reflection_interval_seconds, 1.0)
    next_decay = time.monotonic()
    next_reflect = time.monotonic()
    while True:
        now = time.monotonic()
        if now >= next_decay:
            try:
                run_decay_job()
            except Exception:
                logger.exception("scheduled decay job failed")
            next_decay = time.monotonic() + decay_every
        if now >= next_reflect:
            try:
                run_reflection_agent()
            except Exception:
                logger.exception("scheduled reflection agent failed")
            next_reflect = time.monotonic() + reflect_every
        sleep_for = max(min(next_decay, next_reflect) - time.monotonic(), 0.5)
        time.sleep(sleep_for)


def main() -> None:
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    threading.Thread(target=_maintenance_loop, daemon=True).start()
    worker = Worker([get_queue()], connection=get_redis_connection())
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
