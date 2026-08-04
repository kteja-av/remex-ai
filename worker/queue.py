"""Redis+RQ queue helpers — the only worker module imported by app/api."""

from __future__ import annotations

import json
import threading
from typing import Any
from uuid import uuid4

import redis
from rq import Queue
from rq.job import Job

from app.config import settings

QUEUE_NAME = settings.write_gate_queue_name
SYNC_JOB_PREFIX = "cmis:sync_job:"


def get_redis_connection() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, socket_timeout=5)


def get_queue() -> Queue:
    return Queue(QUEUE_NAME, connection=get_redis_connection())


def queue_depth() -> int:
    return int(get_queue().count)


def queue_has_capacity() -> bool:
    return queue_depth() < settings.write_gate_max_queue_depth


def _sync_job_key(tenant_id: str, job_id: str) -> str:
    return f"{SYNC_JOB_PREFIX}{tenant_id}:{job_id}"


def _store_sync_job(tenant_id: str, job_id: str, result: dict[str, Any]) -> None:
    payload = json.dumps({"status": "finished", "result": result})
    get_redis_connection().set(_sync_job_key(tenant_id, job_id), payload, ex=86_400)


def _mark_sync_job_pending(tenant_id: str, job_id: str) -> None:
    payload = json.dumps({"status": "queued"})
    get_redis_connection().set(_sync_job_key(tenant_id, job_id), payload, ex=86_400)


def enqueue_evaluate(payload: dict[str, Any]) -> str:
    tenant_id = str(payload["tenant_id"])
    if settings.write_gate_sync:
        from worker.write_gate import evaluate_candidate

        job_id = str(uuid4())
        _mark_sync_job_pending(tenant_id, job_id)

        def _run() -> None:
            _store_sync_job(tenant_id, job_id, evaluate_candidate(payload))

        threading.Thread(target=_run, daemon=True).start()
        return job_id

    queue = get_queue()
    job = queue.enqueue(
        "worker.write_gate.evaluate_candidate",
        payload,
        result_ttl=86_400,
        job_timeout=int(settings.write_gate_provider_timeout_seconds) + 60,
    )
    job.meta["tenant_id"] = tenant_id
    job.save_meta()
    return str(job.id)


def get_job_status(job_id: str, tenant_id: str) -> dict[str, Any] | None:
    raw = get_redis_connection().get(_sync_job_key(tenant_id, job_id))
    if isinstance(raw, bytes):
        stored = json.loads(raw.decode())
        return {"job_id": job_id, **stored}

    try:
        job = Job.fetch(job_id, connection=get_redis_connection())
    except Exception:
        return None

    if str(job.meta.get("tenant_id", "")) != tenant_id:
        return None

    status = job.get_status()
    result: dict[str, Any] = {
        "job_id": job.id,
        "status": status,
    }
    if status == "finished" and job.result is not None:
        result["result"] = job.result
    elif status == "failed":
        result["error"] = job.exc_info
    return result


def clear_queue_for_tests() -> None:
    connection = get_redis_connection()
    queue = get_queue()
    queue.empty()
    for key in connection.scan_iter(f"{SYNC_JOB_PREFIX}*"):
        connection.delete(key)


def set_queue_depth_for_tests(depth: int) -> None:
    """Test helper — fill the queue to simulate capacity pressure."""
    queue = get_queue()
    queue.empty()
    for index in range(depth):
        queue.enqueue(
            "worker.write_gate.evaluate_candidate",
            {"placeholder": index},
            result_ttl=60,
        )
