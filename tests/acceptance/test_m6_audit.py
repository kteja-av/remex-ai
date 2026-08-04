import time
import uuid
from collections.abc import Iterator
from dataclasses import replace

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.api.main import app
from app.audit.log import record_audit_event
from app.config import settings
from app.db.session import get_tenant_connection
from app.domain.memory import AuditEvent
from tests.conftest import delete_tenant_memories


@pytest.fixture(autouse=True)
def write_gate_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.embedding.local_encoder import get_encoder

    get_encoder()
    sync_settings = replace(settings, write_gate_sync=True)
    monkeypatch.setattr("app.config.settings", sync_settings)
    monkeypatch.setattr("worker.queue.settings", sync_settings)
    yield


@pytest.fixture()
def identity() -> dict[str, str]:
    return {
        "tenant_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "turn_id": str(uuid.uuid4()),
    }


@pytest.fixture()
def client(identity: dict[str, str]) -> Iterator[TestClient]:
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
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _evaluate(client: TestClient, identity: dict[str, str], content: str) -> dict:
    response = client.post(
        "/v1/memories:evaluate",
        headers=_headers(identity),
        json={
            "type": "semantic",
            "content": content,
            "source_turn_ids": [identity["turn_id"]],
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def _wait_for_job(client: TestClient, identity: dict[str, str], job_id: str) -> dict:
    for _ in range(200):
        response = client.get(f"/v1/jobs/{job_id}", headers=_headers(identity))
        if response.status_code == 404:
            time.sleep(0.15)
            continue
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"finished", "failed"}:
            assert payload["status"] == "finished", payload
            return payload
        time.sleep(0.15)
    raise AssertionError(f"job {job_id} did not finish")


def _audit_rows(tenant_id: str) -> list[dict]:
    with get_tenant_connection(tenant_id) as conn, conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT event, memory_id, actor, source_turn_ids, detail FROM memory_audit"
        ).fetchall()


def test_direct_create_produces_one_admit_audit_row(
    client: TestClient, identity: dict[str, str]
) -> None:
    stored = _store(client, identity, "The user prefers tea in the morning.")
    rows = _audit_rows(identity["tenant_id"])

    assert len(rows) == 1
    assert rows[0]["event"] == AuditEvent.ADMIT.value
    assert str(rows[0]["memory_id"]) == stored["id"]
    assert rows[0]["actor"] == "direct_write"
    assert rows[0]["source_turn_ids"] == [uuid.UUID(identity["turn_id"])]


def test_write_gate_admit_audit_explains_admission(
    client: TestClient, identity: dict[str, str]
) -> None:
    job = _evaluate(client, identity, "The user enjoys hiking on weekends.")
    finished = _wait_for_job(client, identity, job["job_id"])
    memory_id = finished["result"]["memory_id"]

    response = client.get(
        f"/v1/memories/{memory_id}/audit",
        headers=_headers(identity),
    )
    assert response.status_code == 200, response.text
    trail = response.json()

    assert trail["memory_id"] == memory_id
    assert len(trail["events"]) == 1
    event = trail["events"][0]
    assert event["event"] == AuditEvent.ADMIT.value
    assert event["actor"] == "write_gate"
    assert event["source_turn_ids"] == [identity["turn_id"]]
    assert event["detail"]["write_gate_trace"]["judge_verdict"]["verdict"] == "admit"
    assert event["detail"]["write_gate_trace"]["judge_verdict"]["rationale"]


def test_write_gate_reject_produces_one_reject_audit_row(
    client: TestClient, identity: dict[str, str]
) -> None:
    job = _evaluate(
        client,
        identity,
        "The assistant recommended Python as a programming language.",
    )
    finished = _wait_for_job(client, identity, job["job_id"])
    assert finished["result"]["outcome"] == "rejected"

    rows = _audit_rows(identity["tenant_id"])
    assert len(rows) == 1
    assert rows[0]["event"] == AuditEvent.REJECT.value
    assert rows[0]["memory_id"] is None
    assert rows[0]["actor"] == "write_gate"
    assert rows[0]["detail"]["reason"] == "judge_reject"


def test_each_memory_mutation_event_produces_one_audit_row(
    client: TestClient, identity: dict[str, str]
) -> None:
    stored = _store(client, identity, "The user prefers quiet mornings.")
    memory_id = uuid.UUID(stored["id"])
    tenant_id = uuid.UUID(identity["tenant_id"])
    turn_id = uuid.UUID(identity["turn_id"])

    for event in (
        AuditEvent.UPDATE,
        AuditEvent.SUPERSEDE,
        AuditEvent.ARCHIVE,
        AuditEvent.DELETE,
    ):
        record_audit_event(
            tenant_id=tenant_id,
            event=event,
            actor="test",
            source_turn_ids=[turn_id],
            memory_id=memory_id,
            detail={"mutation": event.value},
        )

    response = client.get(
        f"/v1/memories/{memory_id}/audit",
        headers=_headers(identity),
    )
    assert response.status_code == 200, response.text
    events = [entry["event"] for entry in response.json()["events"]]
    assert events == [
        AuditEvent.ADMIT.value,
        AuditEvent.UPDATE.value,
        AuditEvent.SUPERSEDE.value,
        AuditEvent.ARCHIVE.value,
        AuditEvent.DELETE.value,
    ]


def test_audit_table_rejects_update_and_delete_via_app_role(
    client: TestClient, identity: dict[str, str]
) -> None:
    stored = _store(client, identity, "Audit immutability probe.")

    with get_tenant_connection(identity["tenant_id"]) as conn:
        row = conn.execute(
            "SELECT id FROM memory_audit WHERE memory_id = %s",
            (stored["id"],),
        ).fetchone()
        assert row is not None
        audit_id = row[0]

    with get_tenant_connection(identity["tenant_id"]) as conn:
        with pytest.raises(psycopg.Error, match="(?i)append-only|permission denied"):
            conn.execute(
                "UPDATE memory_audit SET actor = 'forged' WHERE id = %s",
                (audit_id,),
            )

    with get_tenant_connection(identity["tenant_id"]) as conn:
        with pytest.raises(psycopg.Error, match="(?i)append-only|permission denied"):
            conn.execute("DELETE FROM memory_audit WHERE id = %s", (audit_id,))


def test_audit_endpoint_returns_404_for_missing_memory(
    client: TestClient, identity: dict[str, str]
) -> None:
    response = client.get(
        f"/v1/memories/{uuid.uuid4()}/audit",
        headers=_headers(identity),
    )
    assert response.status_code == 404


def test_audit_endpoint_is_tenant_scoped(
    client: TestClient, identity: dict[str, str]
) -> None:
    stored = _store(client, identity, "Tenant scoped audit trail.")
    stranger = dict(identity, tenant_id=str(uuid.uuid4()))

    response = client.get(
        f"/v1/memories/{stored['id']}/audit",
        headers=_headers(stranger),
    )
    assert response.status_code == 404


def test_audit_endpoint_requires_memory_owner(
    client: TestClient, identity: dict[str, str]
) -> None:
    stored = _store(client, identity, "Owner scoped audit trail.")
    same_tenant_stranger = dict(identity, user_id=str(uuid.uuid4()))

    response = client.get(
        f"/v1/memories/{stored['id']}/audit",
        headers=_headers(same_tenant_stranger),
    )
    assert response.status_code == 404
