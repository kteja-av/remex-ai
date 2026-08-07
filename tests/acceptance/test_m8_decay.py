"""M8 — decay tiers + reflection derived summaries (off request path)."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.api.main import app
from app.config import settings
from app.db.session import get_connection, get_tenant_connection
from app.domain.memory import AuditEvent, MemoryStatus
from app.embedding.local_encoder import get_encoder
from tests.conftest import delete_tenant_memories
from worker.decay_job import decay_tenant, run_decay_job, target_decay_weight
from worker.reflection_agent import (
    reflect_user,
    run_reflection_agent,
    source_fingerprint,
)


@pytest.fixture()
def identity() -> dict[str, str]:
    return {
        "tenant_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "turn_id": str(uuid.uuid4()),
    }


@pytest.fixture()
def client(identity: dict[str, str]) -> Iterator[TestClient]:
    get_encoder()
    with TestClient(app) as test_client:
        yield test_client
    delete_tenant_memories(identity["tenant_id"])


def _headers(identity: dict[str, str]) -> dict[str, str]:
    return {
        "X-Tenant-ID": identity["tenant_id"],
        "X-User-ID": identity["user_id"],
    }


def _store(client: TestClient, identity: dict[str, str], content: str) -> dict:
    response = client.post(
        "/v1/memories",
        headers=_headers(identity),
        json={
            "type": "semantic",
            "content": content,
            "source_turn_ids": [identity["turn_id"]],
            "importance": 0.8,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _age_memory(tenant_id: str, memory_id: str, *, days: int) -> None:
    accessed_at = datetime.now(UTC) - timedelta(days=days)
    with get_connection() as conn, conn.transaction():
        conn.execute("SET LOCAL row_security = off")
        conn.execute(
            """
            UPDATE memories
            SET created_at = %s,
                updated_at = %s,
                last_accessed_at = %s
            WHERE tenant_id = %s AND id = %s
            """,
            (accessed_at, accessed_at, accessed_at, tenant_id, memory_id),
        )


def _fetch_memory(tenant_id: str, memory_id: str) -> dict:
    with (
        get_tenant_connection(tenant_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        row = cur.execute(
            """
            SELECT id, importance, decay_weight, status, last_accessed_at,
                   write_gate_decision
            FROM memories WHERE id = %s
            """,
            (memory_id,),
        ).fetchone()
    assert row is not None
    return row


def test_target_decay_weight_tiers_are_absolute() -> None:
    assert target_decay_weight(0) == 1.0
    assert target_decay_weight(29.9) == 1.0
    assert target_decay_weight(30) == settings.decay_weight_after_30_days
    assert target_decay_weight(59.9) == settings.decay_weight_after_30_days
    assert target_decay_weight(60) == settings.decay_weight_after_60_days
    assert target_decay_weight(89.9) == settings.decay_weight_after_60_days
    assert target_decay_weight(90) == settings.decay_weight_after_90_days
    assert settings.decay_weight_after_90_days <= settings.decay_archive_threshold


def _only_this_tenant(
    monkeypatch: pytest.MonkeyPatch, identity: dict[str, str]
) -> None:
    """Narrow both schedulers' tenant scans to the test's own tenant/user."""
    tenant_id = uuid.UUID(identity["tenant_id"])
    user_id = uuid.UUID(identity["user_id"])
    monkeypatch.setattr(
        "worker.decay_job._list_active_tenant_ids", lambda: [tenant_id]
    )
    monkeypatch.setattr(
        "worker.reflection_agent._list_tenant_user_pairs",
        lambda: [(tenant_id, user_id)],
    )


def test_unused_age_tiers_reduce_ranking_weight_and_archive(
    client: TestClient, identity: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fresh = _store(client, identity, "Fresh fact about User Alpha preferences.")
    aged_30 = _store(client, identity, "Thirty-day unused note about Project Helios.")
    aged_60 = _store(client, identity, "Sixty-day unused note about Project Helios.")
    aged_90 = _store(client, identity, "Ninety-day unused note about Project Helios.")

    _age_memory(identity["tenant_id"], aged_30["id"], days=30)
    _age_memory(identity["tenant_id"], aged_60["id"], days=60)
    _age_memory(identity["tenant_id"], aged_90["id"], days=90)

    # The scheduled entry point, with its tenant scan narrowed to this test so the
    # result does not depend on unrelated rows left in the shared dev database.
    _only_this_tenant(monkeypatch, identity)
    run_decay_job()

    fresh_row = _fetch_memory(identity["tenant_id"], fresh["id"])
    row_30 = _fetch_memory(identity["tenant_id"], aged_30["id"])
    row_60 = _fetch_memory(identity["tenant_id"], aged_60["id"])
    row_90 = _fetch_memory(identity["tenant_id"], aged_90["id"])

    assert fresh_row["decay_weight"] == 1.0
    assert fresh_row["status"] == MemoryStatus.ACTIVE.value

    assert row_30["decay_weight"] == pytest.approx(settings.decay_weight_after_30_days)
    assert row_30["status"] == MemoryStatus.ACTIVE.value
    ranking_fresh = float(fresh_row["importance"]) * float(fresh_row["decay_weight"])
    ranking_30 = float(row_30["importance"]) * float(row_30["decay_weight"])
    ranking_60 = float(row_60["importance"]) * float(row_60["decay_weight"])
    ranking_90 = float(row_90["importance"]) * float(row_90["decay_weight"])
    assert ranking_30 < ranking_fresh
    assert ranking_60 < ranking_30
    assert ranking_90 < ranking_60

    assert row_60["decay_weight"] == pytest.approx(settings.decay_weight_after_60_days)
    assert row_60["status"] == MemoryStatus.ACTIVE.value

    assert row_90["decay_weight"] == pytest.approx(settings.decay_weight_after_90_days)
    assert row_90["status"] == MemoryStatus.ARCHIVED.value

    with (
        get_tenant_connection(identity["tenant_id"]) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        events = {
            row["event"]
            for row in cur.execute(
                "SELECT event FROM memory_audit WHERE memory_id = %s",
                (aged_90["id"],),
            ).fetchall()
        }
    assert AuditEvent.DECAY.value in events
    assert AuditEvent.ARCHIVE.value in events


def test_decay_job_is_idempotent(
    client: TestClient, identity: dict[str, str]
) -> None:
    stored = _store(client, identity, "Idempotent decay subject about Nova Labs.")
    _age_memory(identity["tenant_id"], stored["id"], days=60)

    # Tenant-scoped so the assertion counts only this test's rows.
    tenant_id = uuid.UUID(identity["tenant_id"])
    first = decay_tenant(tenant_id)
    mid = _fetch_memory(identity["tenant_id"], stored["id"])
    second = decay_tenant(tenant_id)
    end = _fetch_memory(identity["tenant_id"], stored["id"])

    assert len(first) == 1
    assert second == []
    assert mid["decay_weight"] == end["decay_weight"]
    assert mid["status"] == end["status"]

    with (
        get_tenant_connection(identity["tenant_id"]) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        decay_rows = cur.execute(
            """
            SELECT id FROM memory_audit
            WHERE memory_id = %s AND event = 'decay'
            """,
            (stored["id"],),
        ).fetchall()
    assert len(decay_rows) == 1


def test_reflection_creates_derived_summary_linked_to_sources(
    client: TestClient, identity: dict[str, str]
) -> None:
    first = _store(
        client,
        identity,
        "Marcus Chen prefers dark mode in the Orion IDE.",
    )
    second = _store(
        client,
        identity,
        "Marcus Chen reviews pull requests every Friday.",
    )

    created = reflect_user(
        tenant_id=uuid.UUID(identity["tenant_id"]),
        user_id=uuid.UUID(identity["user_id"]),
    )
    assert len(created) == 1

    with (
        get_tenant_connection(identity["tenant_id"]) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        reflection = cur.execute(
            """
            SELECT summary_memory_id, source_memory_ids, source_fingerprint
            FROM memory_reflections
            WHERE user_id = %s
            """,
            (identity["user_id"],),
        ).fetchone()
        assert reflection is not None
        source_ids = {str(value) for value in reflection["source_memory_ids"]}
        assert first["id"] in source_ids
        assert second["id"] in source_ids
        assert reflection["source_fingerprint"] == source_fingerprint(
            [uuid.UUID(first["id"]), uuid.UUID(second["id"])]
        )

        summary = cur.execute(
            """
            SELECT content, write_gate_decision, status
            FROM memories WHERE id = %s
            """,
            (reflection["summary_memory_id"],),
        ).fetchone()
        assert summary is not None
        assert summary["status"] == MemoryStatus.ACTIVE.value
        assert summary["write_gate_decision"]["kind"] == "reflection"
        assert "Marcus Chen" in summary["content"]

        reflect_events = cur.execute(
            """
            SELECT event FROM memory_audit
            WHERE memory_id = %s AND event = 'reflect'
            """,
            (reflection["summary_memory_id"],),
        ).fetchall()
    assert len(reflect_events) == 1


def test_reflection_job_is_idempotent(
    client: TestClient, identity: dict[str, str]
) -> None:
    _store(client, identity, "Avery Quinn uses keyboard shortcuts in Nova Editor.")
    _store(client, identity, "Avery Quinn ships weekly release notes for Nova Editor.")

    scope = {
        "tenant_id": uuid.UUID(identity["tenant_id"]),
        "user_id": uuid.UUID(identity["user_id"]),
    }
    first = reflect_user(**scope)
    second = reflect_user(**scope)

    assert len(first) == 1
    assert second == []


def test_reflection_scheduler_pass_is_idempotent(
    client: TestClient, identity: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _store(client, identity, "Priya Raman tunes the Vega Scheduler weekly.")
    _store(client, identity, "Priya Raman documents Vega Scheduler incidents.")
    _only_this_tenant(monkeypatch, identity)

    assert run_reflection_agent()["created"] == 1
    assert run_reflection_agent()["created"] == 0

    with (
        get_tenant_connection(identity["tenant_id"]) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        rows = cur.execute(
            "SELECT id FROM memory_reflections WHERE user_id = %s",
            (identity["user_id"],),
        ).fetchall()
    assert len(rows) == 1


LOCK_HOLD_SECONDS = 5.0


def test_retrieve_is_not_blocked_by_a_held_row_lock(
    client: TestClient, identity: dict[str, str]
) -> None:
    """The decay job locks the same rows retrieve touches — it must not stall the request.

    The holder releases on its own timer, so a regression shows up as a slow assertion
    failure rather than a hung suite.
    """
    stored = _store(client, identity, "Lock contention probe about Zeta Toolkit.")
    lock_held = threading.Event()
    lock_released = threading.Event()

    def _hold_row_lock() -> None:
        with get_tenant_connection(identity["tenant_id"]) as conn:
            with conn.cursor() as cur:
                # The exact row lock the decay job takes while updating a weight.
                cur.execute(
                    """
                    UPDATE memories
                    SET decay_weight = decay_weight
                    WHERE id = %s AND status = 'active'
                    """,
                    (stored["id"],),
                )
            lock_held.set()
            time.sleep(LOCK_HOLD_SECONDS)
            conn.rollback()
        lock_released.set()

    holder = threading.Thread(target=_hold_row_lock, daemon=True)
    holder.start()
    assert lock_held.wait(timeout=10), "row lock was never acquired"

    started = time.monotonic()
    response = client.get(
        "/v1/memories:retrieve",
        headers=_headers(identity),
        params={"query": "Zeta Toolkit", "limit": 5},
    )
    elapsed = time.monotonic() - started
    # Sampled before joining: joining would release the lock and void the check.
    still_locked = not lock_released.is_set()
    holder.join(timeout=LOCK_HOLD_SECONDS + 10)

    assert response.status_code == 200, response.text
    assert still_locked, "lock was released before the request returned"
    assert elapsed < 2.0, f"retrieve waited on the decay row lock ({elapsed:.2f}s)"
    payload = response.json()
    assert any(item["id"] == stored["id"] for item in payload["memories"])
    assert payload["degraded"] is False


def test_background_jobs_do_not_slow_read_path(
    client: TestClient, identity: dict[str, str]
) -> None:
    aged = [
        _store(client, identity, "Decay load probe one about Zeta Toolkit."),
        _store(client, identity, "Decay load probe two about Zeta Toolkit."),
        _store(client, identity, "Decay load probe three about Zeta Toolkit."),
    ]
    for memory in aged:
        _age_memory(identity["tenant_id"], memory["id"], days=60)

    done = threading.Event()
    tenant_id = uuid.UUID(identity["tenant_id"])
    user_id = uuid.UUID(identity["user_id"])

    def _run_jobs() -> None:
        try:
            decay_tenant(tenant_id)
            reflect_user(tenant_id=tenant_id, user_id=user_id)
        finally:
            done.set()

    worker = threading.Thread(target=_run_jobs, daemon=True)
    worker.start()

    latencies: list[float] = []
    while not done.is_set() and len(latencies) < 10:
        started = time.monotonic()
        response = client.get(
            "/v1/memories:retrieve",
            headers=_headers(identity),
            params={"query": "Zeta Toolkit", "limit": 5},
        )
        latencies.append(time.monotonic() - started)
        assert response.status_code == 200, response.text

    worker.join(timeout=60)
    assert latencies, "no retrieve completed while the jobs ran"
    assert max(latencies) < 2.0, f"read path slowed while jobs ran: {latencies}"


def test_retrieve_touches_last_accessed(
    client: TestClient, identity: dict[str, str]
) -> None:
    stored = _store(client, identity, "Touch probe about Helios Dashboard.")
    _age_memory(identity["tenant_id"], stored["id"], days=10)
    before = _fetch_memory(identity["tenant_id"], stored["id"])["last_accessed_at"]

    response = client.get(
        "/v1/memories:retrieve",
        headers=_headers(identity),
        params={"query": "Helios Dashboard", "limit": 5},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert any(item["id"] == stored["id"] for item in payload["memories"]), payload

    after = _fetch_memory(identity["tenant_id"], stored["id"])["last_accessed_at"]
    assert after > before
