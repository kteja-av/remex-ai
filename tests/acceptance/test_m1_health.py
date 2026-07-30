import os
import time

import httpx
import pytest
import redis

API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
HEARTBEAT_KEY = "cmis:worker:heartbeat"


@pytest.fixture(scope="module")
def health_payload() -> dict:
    response = httpx.get(f"{API_BASE_URL}/v1/health", timeout=10)
    assert response.status_code == 200
    return response.json()


def test_health_reports_ok(health_payload: dict) -> None:
    assert health_payload["status"] == "ok", health_payload


def test_postgres_reachable(health_payload: dict) -> None:
    assert health_payload["dependencies"]["postgres"]["reachable"] is True


def test_pgvector_extension_present(health_payload: dict) -> None:
    assert health_payload["dependencies"]["postgres"]["pgvector"] is True


def test_redis_pong(health_payload: dict) -> None:
    assert health_payload["dependencies"]["redis"]["pong"] is True


def test_worker_heartbeat_fresh(health_payload: dict) -> None:
    worker = health_payload["dependencies"]["worker"]
    assert worker["fresh"] is True
    assert worker["heartbeat_age_seconds"] is not None
    assert worker["heartbeat_age_seconds"] <= worker["stale_after_seconds"]


def test_worker_heartbeat_key_advances() -> None:
    client = redis.Redis.from_url(REDIS_URL, socket_timeout=2)
    first = float(client.get(HEARTBEAT_KEY).decode())
    time.sleep(6)
    second = float(client.get(HEARTBEAT_KEY).decode())
    assert second > first, "worker heartbeat is not advancing"
