import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps_auth import AuthContext, authenticated_identity
from app.context.budgeter import (
    pack_into_budget,
    place_head_tail,
    total_token_count,
)
from app.domain.memory import MemoryType
from app.embedding.local_encoder import Encoder, get_encoder
from app.retrieval.hybrid import retrieve_hybrid
from app.retrieval.vector import touch_last_accessed

router = APIRouter(prefix="/v1", tags=["retrieval"])
logger = logging.getLogger(__name__)

DEFAULT_TOKEN_BUDGET = 512


class RetrievedMemory(BaseModel):
    id: UUID
    type: MemoryType
    content: str
    source_turn_ids: list[UUID]
    created_at: datetime
    score: float


class RetrieveResponse(BaseModel):
    memories: list[RetrievedMemory]
    token_count: int = 0
    degraded: bool = False


def _degraded_response() -> RetrieveResponse:
    return RetrieveResponse(memories=[], token_count=0, degraded=True)


def get_encoder_or_none() -> Encoder | None:
    """Read-path encoder dependency that degrades instead of raising.

    FastAPI solves dependencies before entering the route body, so a model-load
    failure in `get_encoder` would escape the fail-open handler below and 500.
    The write path keeps the raw `get_encoder`: a memory that cannot be embedded
    must fail loudly rather than persist unsearchable.
    """
    try:
        return get_encoder()
    except Exception:
        logger.exception("encoder unavailable; retrieve degrading")
        return None


@router.get("/memories:retrieve", response_model=RetrieveResponse)
def retrieve_memories(
    query: Annotated[str, Query(min_length=1, max_length=10_000)],
    identity: Annotated[AuthContext, Depends(authenticated_identity)],
    encoder: Annotated[Encoder | None, Depends(get_encoder_or_none)],
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
    token_budget: Annotated[int, Query(ge=1, le=100_000)] = DEFAULT_TOKEN_BUDGET,
) -> RetrieveResponse:
    try:
        if encoder is None:
            return _degraded_response()
        result = retrieve_hybrid(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            query=query,
            query_embedding=encoder.encode(query),
            limit=limit,
        )
        retrieved = [
            RetrievedMemory(
                id=hit.memory.id,
                type=hit.memory.type,
                content=hit.memory.content,
                source_turn_ids=hit.memory.source_turn_ids,
                created_at=hit.memory.created_at,
                score=hit.score,
            )
            for hit in result.hits
        ]
        packed = pack_into_budget(retrieved, token_budget)
        placed = place_head_tail(packed)
        try:
            touch_last_accessed(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                memory_ids=[item.id for item in placed],
            )
        except Exception:
            logger.exception("last_accessed touch failed; continuing")
        return RetrieveResponse(
            memories=placed,
            token_count=total_token_count(placed),
            degraded=result.signals_degraded,
        )
    except Exception:
        logger.exception("retrieve degraded", exc_info=True)
        return _degraded_response()
