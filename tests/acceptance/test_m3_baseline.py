import uuid
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.db.session import get_connection, get_tenant_connection
from evals.run import compare_reports, compute_metrics


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


def _store(
    client: TestClient,
    identity: dict[str, str],
    content: str = "My favorite programming language is Python.",
) -> dict:
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


def test_turn_stored_in_one_request_is_retrieved_in_the_next(
    client: TestClient, identity: dict[str, str]
) -> None:
    stored = _store(client, identity)

    response = client.get(
        "/v1/memories:retrieve",
        headers=_headers(identity),
        params={"query": "Which coding language do I prefer?", "limit": 3},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["degraded"] is False
    assert payload["memories"][0]["id"] == stored["id"]
    assert payload["memories"][0]["content"] == stored["content"]
    assert payload["memories"][0]["source_turn_ids"] == [identity["turn_id"]]
    assert 0.0 <= payload["memories"][0]["score"] <= 1.0


def test_retrieval_is_scoped_to_authenticated_tenant_and_user(
    client: TestClient, identity: dict[str, str]
) -> None:
    _store(client, identity, "Tenant A secret preference")

    other_tenant = dict(identity, tenant_id=str(uuid.uuid4()))
    other_user = dict(identity, user_id=str(uuid.uuid4()))
    for stranger in (other_tenant, other_user):
        response = client.get(
            "/v1/memories:retrieve",
            headers=_headers(stranger),
            params={"query": "secret preference"},
        )
        assert response.status_code == 200
        assert response.json()["memories"] == []


@pytest.mark.parametrize("missing_header", ["X-Tenant-ID", "X-User-ID"])
def test_memory_endpoints_require_identity_headers(
    client: TestClient, identity: dict[str, str], missing_header: str
) -> None:
    headers = _headers(identity)
    headers.pop(missing_header)
    response = client.get(
        "/v1/memories:retrieve", headers=headers, params={"query": "anything"}
    )
    assert response.status_code == 422


def test_write_rejects_empty_content_or_provenance(
    client: TestClient, identity: dict[str, str]
) -> None:
    for body in (
        {"type": "semantic", "content": "", "source_turn_ids": [identity["turn_id"]]},
        {"type": "semantic", "content": "fact", "source_turn_ids": []},
    ):
        response = client.post(
            "/v1/memories", headers=_headers(identity), json=body
        )
        assert response.status_code == 422


def test_write_rejects_invalid_identity_uuid(client: TestClient) -> None:
    response = client.post(
        "/v1/memories",
        headers={"X-Tenant-ID": "not-a-uuid", "X-User-ID": str(uuid.uuid4())},
        json={
            "type": "semantic",
            "content": "fact",
            "source_turn_ids": [str(uuid.uuid4())],
        },
    )
    assert response.status_code == 422


def test_database_rejects_empty_provenance(identity: dict[str, str]) -> None:
    with (
        get_tenant_connection(identity["tenant_id"]) as conn,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        conn.execute(
            """
            INSERT INTO memories (
                tenant_id, user_id, type, content, source_turn_ids
            )
            VALUES (%s, %s, 'semantic', 'empty provenance', %s)
            """,
            (identity["tenant_id"], identity["user_id"], []),
        )


def test_eval_metrics_leave_room_for_write_gate_and_retrieval_improvement() -> None:
    naive = compute_metrics(
        admitted={"relevant", "noise"},
        should_admit={"relevant"},
        query_results=[
            {"retrieved": ["relevant", "noise"], "matched": 1},
            {"retrieved": ["noise"], "matched": 0},
        ],
        k=2,
    )
    improved = compute_metrics(
        admitted={"relevant"},
        should_admit={"relevant"},
        query_results=[
            {"retrieved": ["relevant"], "matched": 1},
            {"retrieved": [], "matched": 0},
        ],
        k=2,
    )

    assert naive == {
        "precision": 0.5,
        "recall": 1.0,
        "precision_at_k": pytest.approx(1 / 3),
        "k": 2,
    }
    assert improved["precision"] > naive["precision"]
    assert improved["precision_at_k"] > naive["precision_at_k"]


def test_eval_comparison_rejects_different_labeled_datasets() -> None:
    report = {
        "dataset": {"id": "current"},
        "metrics": {"precision": 1.0, "recall": 1.0, "precision_at_k": 1.0},
    }
    baseline = {
        "dataset": {"id": "other"},
        "metrics": {"precision": 0.0, "recall": 0.0, "precision_at_k": 0.0},
    }
    with pytest.raises(ValueError, match="different labeled datasets"):
        compare_reports(report, baseline)
