import threading
import time

import redis
from rq import Worker

from app.config import settings
from worker.heartbeat import beat, make_client
from worker.queue import get_queue, get_redis_connection


def _heartbeat_loop() -> None:
    client = make_client()
    while True:
        try:
            beat(client)
        except redis.RedisError:
            time.sleep(1)
            client = make_client()
        time.sleep(settings.worker_heartbeat_interval_seconds)


def main() -> None:
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    worker = Worker([get_queue()], connection=get_redis_connection())
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
