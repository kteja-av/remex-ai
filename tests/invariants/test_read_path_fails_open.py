from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routes_retrieve import get_encoder_or_none
from app.embedding.local_encoder import EMBEDDING_DIMENSION


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _fast_encoder() -> MagicMock:
    encoder = MagicMock()
    encoder.encode.return_value = [0.0] * EMBEDDING_DIMENSION
    return encoder


def test_retrieve_never_returns_5xx_when_postgres_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("boom")

    app.dependency_overrides[get_encoder_or_none] = _fast_encoder
    monkeypatch.setattr("app.retrieval.vector.get_tenant_connection", _boom)

    response = client.get(
        "/v1/memories:retrieve",
        headers={
            "X-Tenant-ID": "00000000-0000-4000-8000-000000000001",
            "X-User-ID": "00000000-0000-4000-8000-000000000002",
        },
        params={"query": "probe"},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.status_code < 500


def test_retrieve_never_returns_5xx_when_encoder_fails(client: TestClient) -> None:
    encoder = MagicMock()
    encoder.encode.side_effect = RuntimeError("boom")
    app.dependency_overrides[get_encoder_or_none] = lambda: encoder

    response = client.get(
        "/v1/memories:retrieve",
        headers={
            "X-Tenant-ID": "00000000-0000-4000-8000-000000000001",
            "X-User-ID": "00000000-0000-4000-8000-000000000002",
        },
        params={"query": "probe"},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.status_code < 500


def test_retrieve_failure_payload_is_empty_and_degraded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides[get_encoder_or_none] = _fast_encoder
    monkeypatch.setattr(
        "app.api.routes_retrieve.retrieve_similar",
        MagicMock(side_effect=TimeoutError("timed out")),
    )

    response = client.get(
        "/v1/memories:retrieve",
        headers={
            "X-Tenant-ID": "00000000-0000-4000-8000-000000000003",
            "X-User-ID": "00000000-0000-4000-8000-000000000004",
        },
        params={"query": "probe"},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "memories": [],
        "token_count": 0,
        "degraded": True,
    }
