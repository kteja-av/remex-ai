import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from math import exp
from uuid import UUID

from app.config import RankingWeights, settings
from app.db.models import MemoryRecord
from app.retrieval.entities import extract_entities
from app.retrieval.graph_links import GraphHit, search_graph
from app.retrieval.keyword import KeywordHit, search_keywords
from app.retrieval.vector import VectorHit, retrieve_similar

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RankedHit:
    memory: MemoryRecord
    score: float
    relevance: float
    recency: float
    importance: float


@dataclass(frozen=True)
class RankedRetrieveResult:
    hits: list[RankedHit]
    signals_degraded: bool


def _rrf_fuse(
    *,
    vector_hits: list[VectorHit],
    keyword_hits: list[KeywordHit],
    graph_hits: list[GraphHit],
    weights: RankingWeights,
) -> dict[UUID, float]:
    fused: dict[UUID, float] = {}
    rrf_k = weights.rrf_k
    for rank, vector_hit in enumerate(vector_hits):
        memory_id = vector_hit.memory.id
        fused[memory_id] = fused.get(memory_id, 0.0) + weights.vector / (rrf_k + rank + 1)
    for rank, keyword_hit in enumerate(keyword_hits):
        memory_id = keyword_hit.memory.id
        fused[memory_id] = fused.get(memory_id, 0.0) + weights.keyword / (rrf_k + rank + 1)
    for rank, graph_hit in enumerate(graph_hits):
        memory_id = graph_hit.memory.id
        fused[memory_id] = fused.get(memory_id, 0.0) + weights.graph / (rrf_k + rank + 1)
    return fused


def _normalize_fused_relevance(fused: dict[UUID, float]) -> dict[UUID, float]:
    """Min-max normalize fused RRF scores to [0, 1] preserving magnitude gaps."""
    if not fused:
        return {}
    min_score = min(fused.values())
    max_score = max(fused.values())
    if max_score <= min_score:
        return {memory_id: 1.0 for memory_id in fused}
    span = max_score - min_score
    return {
        memory_id: (score - min_score) / span for memory_id, score in fused.items()
    }


def _memory_lookup(
    vector_hits: list[VectorHit],
    keyword_hits: list[KeywordHit],
    graph_hits: list[GraphHit],
) -> dict[UUID, MemoryRecord]:
    records: dict[UUID, MemoryRecord] = {}
    for hits in (vector_hits, keyword_hits, graph_hits):
        for hit in hits:
            records[hit.memory.id] = hit.memory
    return records


def recency_score(created_at: datetime, *, half_life_days: float) -> float:
    now = datetime.now(UTC)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    age_days = max((now - created_at).total_seconds() / 86_400.0, 0.0)
    return exp(-age_days / half_life_days)


def importance_score(memory: MemoryRecord) -> float:
    return max(0.0, min(1.0, memory.importance * memory.decay_weight))


def _tiebreak_multiplier(
    *,
    recency: float,
    importance: float,
    weights: RankingWeights,
) -> float:
    # Small additive-style boost — capped so relevance magnitude always dominates.
    return 1.0 + 0.02 * (
        weights.recency_exponent * (recency - 0.5)
        + weights.importance_exponent * (importance - 0.5)
    )


def rank_memories(
    *,
    tenant_id: UUID,
    user_id: UUID,
    query: str,
    query_embedding: list[float],
    limit: int,
    weights: RankingWeights | None = None,
) -> RankedRetrieveResult:
    active_weights = weights or settings.ranking_weights
    candidate_limit = max(limit * active_weights.candidate_multiplier, limit)
    signals_attempted = 0
    graph_failed = False

    vector_failed = False
    signals_attempted += 1
    try:
        vector_hits = retrieve_similar(
            tenant_id=tenant_id,
            user_id=user_id,
            query_embedding=query_embedding,
            limit=candidate_limit,
        )
    except Exception:
        vector_failed = True
        logger.exception("vector retrieval failed; continuing with other signals")
        vector_hits = []

    keyword_failed = False
    signals_attempted += 1
    try:
        keyword_hits = search_keywords(
            tenant_id=tenant_id,
            user_id=user_id,
            query=query,
            limit=candidate_limit,
        )
    except Exception:
        keyword_failed = True
        logger.exception("keyword retrieval failed; continuing with other signals")
        keyword_hits = []

    if extract_entities(query):
        signals_attempted += 1
        try:
            graph_hits = search_graph(
                tenant_id=tenant_id,
                user_id=user_id,
                query=query,
                limit=candidate_limit,
            )
        except Exception:
            graph_failed = True
            logger.exception("graph retrieval failed; continuing with other signals")
            graph_hits = []
    else:
        graph_hits = []

    if vector_failed and keyword_failed:
        raise RuntimeError("primary retrieval signals failed")

    signals_degraded = vector_failed or keyword_failed or graph_failed

    fused = _rrf_fuse(
        vector_hits=vector_hits,
        keyword_hits=keyword_hits,
        graph_hits=graph_hits,
        weights=active_weights,
    )
    relevance_by_id = _normalize_fused_relevance(fused)
    records = _memory_lookup(vector_hits, keyword_hits, graph_hits)
    ranked: list[RankedHit] = []
    for memory_id, relevance in relevance_by_id.items():
        memory = records[memory_id]
        recency = recency_score(
            memory.created_at, half_life_days=active_weights.recency_half_life_days
        )
        importance = importance_score(memory)
        score = min(
            1.0,
            (relevance**active_weights.relevance_exponent)
            * _tiebreak_multiplier(
                recency=recency, importance=importance, weights=active_weights
            ),
        )
        ranked.append(
            RankedHit(
                memory=memory,
                score=score,
                relevance=relevance,
                recency=recency,
                importance=importance,
            )
        )
    ranked.sort(key=lambda hit: hit.score, reverse=True)
    return RankedRetrieveResult(
        hits=ranked[:limit],
        signals_degraded=signals_degraded,
    )
