import time
import uuid
from collections.abc import Iterator
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routes_retrieve import get_encoder_or_none
from app.config import settings
from app.db.session import get_connection, get_tenant_connection
from app.domain.policy import AdmissionVerdict, JudgeVerdict
from app.embedding.local_encoder import EMBEDDING_DIMENSION
from evals.run import compare_reports, evaluate_suite
from psycopg.rows import dict_row
from worker import llm_providers
from worker.queue import clear_queue_for_tests, set_queue_depth_for_tests
from worker.write_gate import set_judge_delay


@pytest.fixture(autouse=True)
def write_gate_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.embedding.local_encoder import get_encoder

    get_encoder()
    sync_settings = replace(settings, write_gate_sync=True)
    monkeypatch.setattr("app.config.settings", sync_settings)
    monkeypatch.setattr("worker.queue.settings", sync_settings)
    set_judge_delay(0.0)
    clear_queue_for_tests()
    yield
    clear_queue_for_tests()


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
    with get_connection() as conn, conn.transaction():
        conn.execute("SET LOCAL row_security = off")
        conn.execute(
            "DELETE FROM memories WHERE tenant_id = %s", (identity["tenant_id"],)
        )


def _headers(identity: dict[str, str]) -> dict[str, str]:
    return {
        "X-Tenant-ID": identity["tenant_id"],
        "X-User-ID": identity["user_id"],
    }


def _evaluate(
    client: TestClient,
    identity: dict[str, str],
    *,
    content: str,
    turn_id: str | None = None,
) -> dict:
    response = client.post(
        "/v1/memories:evaluate",
        headers=_headers(identity),
        json={
            "type": "semantic",
            "content": content,
            "source_turn_ids": [turn_id or identity["turn_id"]],
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


def test_evaluate_returns_job_id_immediately(
    client: TestClient, identity: dict[str, str]
) -> None:
    started = time.monotonic()
    payload = _evaluate(
        client,
        identity,
        content="The user prefers tea in the morning.",
    )
    elapsed = time.monotonic() - started
    assert "job_id" in payload
    assert elapsed < 1.0


def test_write_gate_admits_user_facts_and_rejects_assistant_noise(
    client: TestClient, identity: dict[str, str]
) -> None:
    admitted = _evaluate(
        client, identity, content="The user prefers Python for programming."
    )
    rejected = _evaluate(
        client,
        identity,
        content="The assistant recommended Python as a programming language.",
        turn_id=str(uuid.uuid4()),
    )

    admitted_job = _wait_for_job(client, identity, admitted["job_id"])
    rejected_job = _wait_for_job(client, identity, rejected["job_id"])

    assert admitted_job["result"]["outcome"] == "admitted"
    assert rejected_job["result"]["outcome"] == "rejected"

    with get_tenant_connection(identity["tenant_id"]) as conn, conn.cursor(
        row_factory=dict_row
    ) as cur:
        rows = cur.execute(
            "SELECT content FROM memories WHERE tenant_id = %s",
            (identity["tenant_id"],),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["content"] == "The user prefers Python for programming."


def test_admitted_memory_carries_replayable_write_gate_trace(
    client: TestClient, identity: dict[str, str]
) -> None:
    job = _evaluate(client, identity, content="The user enjoys hiking on weekends.")
    finished = _wait_for_job(client, identity, job["job_id"])
    trace = finished["result"]["trace"]

    assert trace["source_turn_ids"] == [identity["turn_id"]]
    assert trace["pii_verdict"]["status"] == "clean"
    assert trace["judge_verdict"]["verdict"] == "admit"

    with get_tenant_connection(identity["tenant_id"]) as conn, conn.cursor(
        row_factory=dict_row
    ) as cur:
        row = cur.execute(
            """
            SELECT write_gate_decision
            FROM memories
            WHERE tenant_id = %s
            """,
            (identity["tenant_id"],),
        ).fetchone()
    assert row is not None
    assert row["write_gate_decision"]["trace_id"] == trace["trace_id"]


def test_pii_bearing_candidate_never_reaches_provider(
    client: TestClient,
    identity: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _spy(_content: str) -> JudgeVerdict:
        calls.append("judge")
        return JudgeVerdict(
            verdict=AdmissionVerdict.ADMIT,
            rationale="should not run",
            provider="spy",
        )

    monkeypatch.setattr(llm_providers, "judge_with_fallback", _spy)

    job = _evaluate(
        client,
        identity,
        content="Contact me at alice@example.com for updates.",
        turn_id=str(uuid.uuid4()),
    )
    finished = _wait_for_job(client, identity, job["job_id"])

    assert finished["result"]["outcome"] == "rejected"
    assert finished["result"]["reason"] == "pii_blocked"
    assert calls == []


def test_queue_at_capacity_returns_429_without_blocking(
    client: TestClient,
    identity: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_settings = replace(
        settings,
        write_gate_sync=False,
        write_gate_max_queue_depth=2,
    )
    monkeypatch.setattr("app.config.settings", async_settings)
    monkeypatch.setattr("worker.queue.settings", async_settings)
    set_queue_depth_for_tests(async_settings.write_gate_max_queue_depth)

    response = client.post(
        "/v1/memories:evaluate",
        headers=_headers(identity),
        json={
            "type": "semantic",
            "content": "The user prefers quiet mornings.",
            "source_turn_ids": [identity["turn_id"]],
        },
    )
    assert response.status_code == 429


def test_judge_delay_does_not_slow_read_path(
    client: TestClient, identity: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    set_judge_delay(30.0)
    _evaluate(client, identity, content="The user prefers jazz music.")

    app.dependency_overrides[get_encoder_or_none] = lambda: MagicMock(
        encode=MagicMock(return_value=[0.0] * EMBEDDING_DIMENSION)
    )
    started = time.monotonic()
    response = client.get(
        "/v1/memories:retrieve",
        headers=_headers(identity),
        params={"query": "music preference", "limit": 3},
    )
    elapsed = time.monotonic() - started
    app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert elapsed < 1.0


def test_job_status_isolated_by_tenant(
    client: TestClient, identity: dict[str, str]
) -> None:
    job = _evaluate(client, identity, content="The user prefers Python for programming.")
    stranger = dict(identity, tenant_id=str(uuid.uuid4()))

    response = client.get(
        f"/v1/jobs/{job['job_id']}",
        headers=_headers(stranger),
    )
    assert response.status_code == 404


def test_write_gate_eval_beats_baseline_metrics() -> None:
    report = evaluate_suite("write_gate")
    baseline_path = (
        __import__("pathlib").Path("evals") / "reports" / "baseline.json"
    )
    if baseline_path.exists():
        baseline = __import__("json").loads(
            baseline_path.read_text(encoding="utf-8")
        )
        deltas = compare_reports(report, baseline)
        assert deltas["precision"] > 0.0
        assert report["metrics"]["recall"] == baseline["metrics"]["recall"]
    else:
        assert report["metrics"]["precision"] == 1.0
        assert report["metrics"]["recall"] == 1.0
