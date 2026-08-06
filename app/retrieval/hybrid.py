from dataclasses import dataclass
from uuid import UUID

from app.ranking.scorer import RankedHit, rank_memories


@dataclass(frozen=True)
class HybridRetrieveResult:
    hits: list[RankedHit]
    signals_degraded: bool


def retrieve_hybrid(
    *,
    tenant_id: UUID,
    user_id: UUID,
    query: str,
    query_embedding: list[float],
    limit: int,
) -> HybridRetrieveResult:
    result = rank_memories(
        tenant_id=tenant_id,
        user_id=user_id,
        query=query,
        query_embedding=query_embedding,
        limit=limit,
    )
    return HybridRetrieveResult(
        hits=result.hits,
        signals_degraded=result.signals_degraded,
    )
