import time
import uuid
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routes_retrieve import get_encoder_or_none
from app.context.budgeter import estimate_tokens, place_head_tail
from tests.conftest import delete_tenant_memories
from app.embedding.local_encoder import EMBEDDING_DIMENSION


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


def _fast_encoder() -> MagicMock:
    encoder = MagicMock()
    encoder.encode.return_value = [0.0] * EMBEDDING_DIMENSION
    return encoder


def _headers(identity: dict[str, str]) -> dict[str, str]:
    return {
        "X-Tenant-ID": identity["tenant_id"],
        "X-User-ID": identity["user_id"],
    }


def _store(
    client: TestClient,
    identity: dict[str, str],
    *,
    content: str,
    turn_id: str | None = None,
) -> dict:
    response = client.post(
        "/v1/memories",
        headers=_headers(identity),
        json={
            "type": "semantic",
            "content": content,
            "source_turn_ids": [turn_id or identity["turn_id"]],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_retrieve_returns_degraded_empty_when_postgres_unreachable(
    client: TestClient, identity: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("postgres unreachable")

    app.dependency_overrides[get_encoder_or_none] = _fast_encoder
    for module in (
        "app.retrieval.vector",
        "app.retrieval.keyword",
        "app.retrieval.graph_links",
    ):
        monkeypatch.setattr(f"{module}.get_read_tenant_connection", _boom)

    started = time.monotonic()
    response = client.get(
        "/v1/memories:retrieve",
        headers=_headers(identity),
        params={"query": "anything"},
    )
    elapsed = time.monotonic() - started
    app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json() == {
        "memories": [],
        "token_count": 0,
        "degraded": True,
    }
    assert elapsed < 5.0


def test_retrieve_returns_degraded_empty_when_encoder_fails(
    client: TestClient, identity: dict[str, str]
) -> None:
    encoder = MagicMock()
    encoder.encode.side_effect = RuntimeError("encoder failed")
    app.dependency_overrides[get_encoder_or_none] = lambda: encoder

    response = client.get(
        "/v1/memories:retrieve",
        headers=_headers(identity),
        params={"query": "anything"},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json()["degraded"] is True
    assert response.json()["memories"] == []


def test_retrieve_returns_degraded_when_encoder_load_fails(
    identity: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Model-load failure is resolved by FastAPI before the route body runs, so it
    must be caught in the dependency or it escapes the fail-open handler as a 500."""
    monkeypatch.setattr(
        "app.api.routes_retrieve.get_encoder",
        MagicMock(side_effect=RuntimeError("model artifacts missing")),
    )

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get(
            "/v1/memories:retrieve",
            headers=_headers(identity),
            params={"query": "anything"},
        )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "memories": [],
        "token_count": 0,
        "degraded": True,
    }


def test_token_budget_limits_returned_memories(
    client: TestClient, identity: dict[str, str]
) -> None:
    for index in range(4):
        _store(
            client,
            identity,
            content=f"Memory number {index} about project planning details.",
            turn_id=str(uuid.uuid4()),
        )

    token_budget = estimate_tokens("Memory number 0 about project planning details.")
    response = client.get(
        "/v1/memories:retrieve",
        headers=_headers(identity),
        params={
            "query": "project planning",
            "limit": 4,
            "token_budget": token_budget,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["degraded"] is False
    assert len(payload["memories"]) == 1
    assert payload["token_count"] <= token_budget


def test_highest_ranked_memories_are_placed_at_head_and_tail() -> None:
    ranked = ["best", "second", "third", "fourth"]
    placed = place_head_tail(ranked)
    assert placed == ["best", "third", "fourth", "second"]


def test_retrieve_applies_head_tail_placement_to_ranked_hits(
    client: TestClient, identity: dict[str, str]
) -> None:
    contents = [
        "Alpha is my primary programming language for backend services.",
        "Beta is my secondary scripting language for automation tasks.",
        "Gamma is my favorite database technology for analytics workloads.",
        "Delta is my preferred cloud provider for production deployments.",
    ]
    stored = [
        _store(client, identity, content=content, turn_id=str(uuid.uuid4()))
        for content in contents
    ]

    response = client.get(
        "/v1/memories:retrieve",
        headers=_headers(identity),
        params={
            "query": "What programming language do I use for backend services?",
            "limit": 4,
            "token_budget": 10_000,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["degraded"] is False
    assert len(payload["memories"]) == 4

    by_id = {item["id"]: item for item in payload["memories"]}
    alpha = by_id[stored[0]["id"]]
    beta = by_id[stored[1]["id"]]
    assert payload["memories"][0]["id"] == alpha["id"]
    assert payload["memories"][-1]["id"] == beta["id"]
    assert alpha["score"] >= beta["score"]


def test_every_returned_memory_includes_provenance(
    client: TestClient, identity: dict[str, str]
) -> None:
    turn_id = str(uuid.uuid4())
    _store(
        client,
        identity,
        content="I prefer tea over coffee in the morning.",
        turn_id=turn_id,
    )

    response = client.get(
        "/v1/memories:retrieve",
        headers=_headers(identity),
        params={"query": "morning beverage preference"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["degraded"] is False
    assert payload["memories"]
    for memory in payload["memories"]:
        assert memory["source_turn_ids"] == [turn_id]


def test_happy_path_reports_token_count(
    client: TestClient, identity: dict[str, str]
) -> None:
    content = "My favorite color is blue."
    _store(client, identity, content=content)

    response = client.get(
        "/v1/memories:retrieve",
        headers=_headers(identity),
        params={"query": "favorite color"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["degraded"] is False
    assert payload["token_count"] == estimate_tokens(content)
    assert payload["token_count"] > 0
