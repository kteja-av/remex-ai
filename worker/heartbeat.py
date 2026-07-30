import time

import redis

from app.config import settings

HEARTBEAT_KEY = "cmis:worker:heartbeat"


def beat(client: redis.Redis) -> None:
    client.set(HEARTBEAT_KEY, str(time.time()))


def make_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, socket_timeout=5)
