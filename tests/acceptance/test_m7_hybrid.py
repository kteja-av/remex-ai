import json
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.config import RankingWeights, settings
from app.domain.memory import MemoryType
from app.db.session import get_tenant_connection
from app.embedding.local_encoder import get_encoder
from app.ranking.scorer import rank_memories, recency_score
from app.retrieval.entities import extract_entities
from app.retrieval.graph_links import search_graph
from app.retrieval.hybrid import retrieve_hybrid
from app.retrieval.keyword import search_keywords
from app.retrieval.vector import store_memory
from evals.run import evaluate_suite
from tests.conftest import delete_tenant_memories


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


def test_keyword_search_finds_exact_name(identity: dict[str, str]) -> None:
    encoder = get_encoder()
    tenant_id = uuid.UUID(identity["tenant_id"])
    user_id = uuid.UUID(identity["user_id"])
    record = store_memory(
        tenant_id=tenant_id,
        user_id=user_id,
        memory_type=MemoryType.SEMANTIC,
        content="The user's dog is named Xerophyte.",
        source_turn_ids=[uuid.UUID(identity["turn_id"])],
        embedding=encoder.encode("The user's dog is named Xerophyte."),
    )
    hits = search_keywords(
        tenant_id=tenant_id,
        user_id=user_id,
        query="Xerophyte",
        limit=3,
    )
    assert hits
    assert hits[0].memory.id == record.id


def test_graph_search_links_entities(identity: dict[str, str]) -> None:
    encoder = get_encoder()
    tenant_id = uuid.UUID(identity["tenant_id"])
    user_id = uuid.UUID(identity["user_id"])
    manager = store_memory(
        tenant_id=tenant_id,
        user_id=user_id,
        memory_type=MemoryType.SEMANTIC,
        content="Dr. Helena Voss manages the neurology research lab.",
        source_turn_ids=[uuid.uuid4()],
        embedding=encoder.encode("Dr. Helena Voss manages the neurology research lab."),
    )
    report = store_memory(
        tenant_id=tenant_id,
        user_id=user_id,
        memory_type=MemoryType.SEMANTIC,
        content="Marcus Chen reports directly to Dr. Helena Voss.",
        source_turn_ids=[uuid.uuid4()],
        embedding=encoder.encode("Marcus Chen reports directly to Dr. Helena Voss."),
    )
    assert "Marcus Chen" in extract_entities(report.content)
    hits = search_graph(
        tenant_id=tenant_id,
        user_id=user_id,
        query="Who does Marcus Chen report to?",
        limit=3,
    )
    labels = {hit.memory.id for hit in hits}
    assert report.id in labels
    assert manager.id in labels


def test_hybrid_beats_vector_only_on_exact_name_query(
    identity: dict[str, str],
) -> None:
    encoder = get_encoder()
    tenant_id = uuid.UUID(identity["tenant_id"])
    user_id = uuid.UUID(identity["user_id"])
    exact = store_memory(
        tenant_id=tenant_id,
        user_id=user_id,
        memory_type=MemoryType.SEMANTIC,
        content="The user's dog is named Xerophyte.",
        source_turn_ids=[uuid.uuid4()],
        embedding=encoder.encode("The user's dog is named Xerophyte."),
    )
    store_memory(
        tenant_id=tenant_id,
        user_id=user_id,
        memory_type=MemoryType.SEMANTIC,
        content="The user mentioned having a pet animal at home.",
        source_turn_ids=[uuid.uuid4()],
        embedding=encoder.encode("The user mentioned having a pet animal at home."),
    )
    query = "What is the dog named Xerophyte?"
    hybrid_result = retrieve_hybrid(
        tenant_id=tenant_id,
        user_id=user_id,
        query=query,
        query_embedding=encoder.encode(query),
        limit=2,
    )
    hybrid_hits = hybrid_result.hits
    assert hybrid_hits[0].memory.id == exact.id
    assert hybrid_hits[0].relevance >= hybrid_hits[-1].relevance


def test_retrieve_endpoint_uses_hybrid_ranking(
    client: TestClient, identity: dict[str, str]
) -> None:
    response = client.post(
        "/v1/memories",
        headers=_headers(identity),
        json={
            "type": "semantic",
            "content": "The user's project codename is Nightjar-17.",
            "source_turn_ids": [identity["turn_id"]],
        },
    )
    assert response.status_code == 201
    stored = response.json()

    retrieve = client.get(
        "/v1/memories:retrieve",
        headers=_headers(identity),
        params={"query": "project codename Nightjar-17", "limit": 3},
    )
    assert retrieve.status_code == 200
    payload = retrieve.json()
    assert payload["degraded"] is False
    assert payload["memories"][0]["id"] == stored["id"]


def test_ranking_weights_live_in_config_not_code() -> None:
    assert settings.ranking_weights.vector > 0
    assert settings.ranking_weights.keyword > 0
    assert settings.ranking_weights.graph > 0
    source = Path("app/config.py").read_text(encoding="utf-8")
    assert "RANKING_VECTOR_WEIGHT" in source
    assert "RANKING_KEYWORD_WEIGHT" in source
    assert "RANKING_GRAPH_WEIGHT" in source


def test_ranking_applies_recency_and_importance(identity: dict[str, str]) -> None:
    encoder = get_encoder()
    tenant_id = uuid.UUID(identity["tenant_id"])
    user_id = uuid.UUID(identity["user_id"])
    store_memory(
        tenant_id=tenant_id,
        user_id=user_id,
        memory_type=MemoryType.SEMANTIC,
        content="Nightjar-17 is the project codename.",
        source_turn_ids=[uuid.uuid4()],
        embedding=encoder.encode("Nightjar-17 is the project codename."),
        importance=0.9,
    )
    result = rank_memories(
        tenant_id=tenant_id,
        user_id=user_id,
        query="Nightjar-17",
        query_embedding=encoder.encode("Nightjar-17"),
        limit=1,
        weights=RankingWeights(
            vector=0.45,
            keyword=0.35,
            graph=0.20,
            relevance_exponent=1.0,
            recency_exponent=0.35,
            importance_exponent=0.65,
            recency_half_life_days=30.0,
            rrf_k=60.0,
            candidate_multiplier=3,
        ),
    )
    hits = result.hits
    assert hits
    assert 0.0 < hits[0].recency <= 1.0
    assert hits[0].importance > 0.5


def test_entity_links_indexed_on_store(identity: dict[str, str]) -> None:
    encoder = get_encoder()
    tenant_id = uuid.UUID(identity["tenant_id"])
    user_id = uuid.UUID(identity["user_id"])
    record = store_memory(
        tenant_id=tenant_id,
        user_id=user_id,
        memory_type=MemoryType.SEMANTIC,
        content="Marcus Chen reports directly to Dr. Helena Voss.",
        source_turn_ids=[uuid.uuid4()],
        embedding=encoder.encode("Marcus Chen reports directly to Dr. Helena Voss."),
    )
    with get_tenant_connection(identity["tenant_id"]) as conn, conn.cursor() as cur:
        rows = cur.execute(
            """
            SELECT entity FROM memory_entity_links
            WHERE memory_id = %s
            ORDER BY entity
            """,
            (record.id,),
        ).fetchall()
    entities = [row[0] for row in rows]
    assert "Marcus Chen" in entities
    assert "Dr. Helena Voss" in entities


def test_hybrid_eval_improves_precision_at_k_over_vector_only() -> None:
    report = evaluate_suite("hybrid")
    hybrid_pak = report["metrics"]["precision_at_k"]
    vector_pak = report["vector_only_on_suite"]["metrics"]["precision_at_k"]
    assert hybrid_pak > vector_pak
    assert report["comparison_on_suite"]["precision_at_k"] > 0


def test_hybrid_eval_compare_accepts_cross_dataset_baseline() -> None:
    report = evaluate_suite("hybrid")
    baseline = json.loads(
        Path("evals/reports/baseline.json").read_text(encoding="utf-8")
    )
    from evals.run import compare_reports

    comparison = compare_reports(report, baseline)
    assert comparison["basis"] == "vector_only_on_suite"
    assert comparison["precision_at_k"] == pytest.approx(
        report["comparison_on_suite"]["precision_at_k"]
    )


def test_recency_score_decays_with_age() -> None:
    from datetime import UTC, datetime, timedelta

    fresh = recency_score(datetime.now(UTC), half_life_days=30.0)
    old = recency_score(
        datetime.now(UTC) - timedelta(days=90), half_life_days=30.0
    )
    assert fresh > old


def test_relevance_beats_importance_for_exact_name_match(
    identity: dict[str, str],
) -> None:
    encoder = get_encoder()
    tenant_id = uuid.UUID(identity["tenant_id"])
    user_id = uuid.UUID(identity["user_id"])
    exact = store_memory(
        tenant_id=tenant_id,
        user_id=user_id,
        memory_type=MemoryType.SEMANTIC,
        content="The user's dog is named Xerophyte.",
        source_turn_ids=[uuid.uuid4()],
        embedding=encoder.encode("The user's dog is named Xerophyte."),
        importance=0.1,
    )
    store_memory(
        tenant_id=tenant_id,
        user_id=user_id,
        memory_type=MemoryType.SEMANTIC,
        content="The user mentioned having a pet animal at home.",
        source_turn_ids=[uuid.uuid4()],
        embedding=encoder.encode("The user mentioned having a pet animal at home."),
        importance=0.95,
    )
    result = rank_memories(
        tenant_id=tenant_id,
        user_id=user_id,
        query="What is the dog named Xerophyte?",
        query_embedding=encoder.encode("What is the dog named Xerophyte?"),
        limit=2,
    )
    assert result.hits[0].memory.id == exact.id


def test_partial_signal_failure_still_returns_other_hits(
    identity: dict[str, str],
) -> None:
    encoder = get_encoder()
    tenant_id = uuid.UUID(identity["tenant_id"])
    user_id = uuid.UUID(identity["user_id"])
    record = store_memory(
        tenant_id=tenant_id,
        user_id=user_id,
        memory_type=MemoryType.SEMANTIC,
        content="The user prefers Python for programming.",
        source_turn_ids=[uuid.uuid4()],
        embedding=encoder.encode("The user prefers Python for programming."),
    )
    with patch(
        "app.ranking.scorer.search_keywords",
        side_effect=ConnectionError("keyword down"),
    ):
        result = rank_memories(
            tenant_id=tenant_id,
            user_id=user_id,
            query="Which coding language does the user like?",
            query_embedding=encoder.encode("Which coding language does the user like?"),
            limit=3,
        )
    assert result.hits
    assert result.hits[0].memory.id == record.id
    assert result.signals_degraded is True


def test_hybrid_retrieve_stays_within_latency_budget_after_writes(
    identity: dict[str, str],
) -> None:
    encoder = get_encoder()
    tenant_id = uuid.UUID(identity["tenant_id"])
    user_id = uuid.UUID(identity["user_id"])
    for index in range(150):
        store_memory(
            tenant_id=tenant_id,
            user_id=user_id,
            memory_type=MemoryType.SEMANTIC,
            content=(
                f"Marcus Chen works on project Nightjar-{index} "
                "in the neurology research lab."
            ),
            source_turn_ids=[uuid.uuid4()],
            embedding=encoder.encode(
                f"Marcus Chen works on project Nightjar-{index} "
                "in the neurology research lab."
            ),
        )

    started = time.monotonic()
    graph_hits = search_graph(
        tenant_id=tenant_id,
        user_id=user_id,
        query="Marcus Chen",
        limit=5,
    )
    elapsed = time.monotonic() - started

    assert graph_hits
    assert elapsed < 0.5


def test_relevance_beats_importance_at_high_limit(
    identity: dict[str, str],
) -> None:
    encoder = get_encoder()
    tenant_id = uuid.UUID(identity["tenant_id"])
    user_id = uuid.UUID(identity["user_id"])
    exact = store_memory(
        tenant_id=tenant_id,
        user_id=user_id,
        memory_type=MemoryType.SEMANTIC,
        content="The user's dog is named Xerophyte.",
        source_turn_ids=[uuid.uuid4()],
        embedding=encoder.encode("The user's dog is named Xerophyte."),
        importance=0.05,
    )
    for index in range(60):
        store_memory(
            tenant_id=tenant_id,
            user_id=user_id,
            memory_type=MemoryType.SEMANTIC,
            content=f"Generic filler memory number {index} about unrelated topics.",
            source_turn_ids=[uuid.uuid4()],
            embedding=encoder.encode(f"Generic filler memory number {index}."),
            importance=0.95,
        )
    result = rank_memories(
        tenant_id=tenant_id,
        user_id=user_id,
        query="What is the dog named Xerophyte?",
        query_embedding=encoder.encode("What is the dog named Xerophyte?"),
        limit=15,
    )
    assert result.hits[0].memory.id == exact.id


def test_primary_signal_failure_degrades_retrieve(
    client: TestClient, identity: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _store = client.post(
        "/v1/memories",
        headers=_headers(identity),
        json={
            "type": "semantic",
            "content": "The user prefers Python for programming.",
            "source_turn_ids": [identity["turn_id"]],
        },
    )
    assert _store.status_code == 201

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("postgres unreachable")

    for target in (
        "app.ranking.scorer.retrieve_similar",
        "app.ranking.scorer.search_keywords",
    ):
        monkeypatch.setattr(target, _boom)

    response = client.get(
        "/v1/memories:retrieve",
        headers=_headers(identity),
        params={"query": "Which coding language does the user like?"},
    )
    assert response.status_code == 200
    assert response.json()["degraded"] is True
