import time

import redis

from app.config import settings
from worker.heartbeat import beat, make_client


def main() -> None:
    client = make_client()
    while True:
        try:
            beat(client)
        except redis.RedisError:
            time.sleep(1)
            client = make_client()
        time.sleep(settings.worker_heartbeat_interval_seconds)


if __name__ == "__main__":
    main()
