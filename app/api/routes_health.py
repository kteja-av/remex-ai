import time
from typing import Any, cast

import psycopg
import redis
from fastapi import APIRouter

from app.config import settings
from app.db.session import get_connection

router = APIRouter()

HEARTBEAT_KEY = "cmis:worker:heartbeat"


def _check_postgres() -> dict[str, Any]:
    result: dict[str, Any] = {"reachable": False, "pgvector": False}
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT 1").fetchone()
            result["reachable"] = row is not None and row[0] == 1
            ext = conn.execute(
                "SELECT COUNT(*) FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
            result["pgvector"] = ext is not None and ext[0] > 0
    except psycopg.Error:
        pass
    return result


def _check_redis() -> dict[str, Any]:
    try:
        client = redis.Redis.from_url(settings.redis_url, socket_timeout=2)
        return {"pong": bool(client.ping())}
    except redis.RedisError:
        return {"pong": False}


def _check_worker() -> dict[str, Any]:
    result: dict[str, Any] = {
        "heartbeat_age_seconds": None,
        "fresh": False,
        "stale_after_seconds": settings.worker_heartbeat_stale_after_seconds,
    }
    try:
        client = redis.Redis.from_url(settings.redis_url, socket_timeout=2)
        raw = cast(bytes | None, client.get(HEARTBEAT_KEY))
        if raw is not None:
            age = time.time() - float(raw.decode())
            result["heartbeat_age_seconds"] = round(age, 3)
            result["fresh"] = age <= settings.worker_heartbeat_stale_after_seconds
    except (redis.RedisError, ValueError):
        pass
    return result


@router.get("/v1/health")
def health() -> dict[str, Any]:
    dependencies = {
        "postgres": _check_postgres(),
        "redis": _check_redis(),
        "worker": _check_worker(),
    }
    ok = (
        dependencies["postgres"]["reachable"]
        and dependencies["postgres"]["pgvector"]
        and dependencies["redis"]["pong"]
        and dependencies["worker"]["fresh"]
    )
    return {"status": "ok" if ok else "degraded", "dependencies": dependencies}
