from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps_auth import AuthContext, authenticated_identity
from app.domain.memory import MemoryType
from app.embedding.local_encoder import Encoder, get_encoder
from app.retrieval.vector import retrieve_similar

router = APIRouter(prefix="/v1", tags=["retrieval"])


class RetrievedMemory(BaseModel):
    id: UUID
    type: MemoryType
    content: str
    source_turn_ids: list[UUID]
    created_at: datetime
    score: float


class RetrieveResponse(BaseModel):
    memories: list[RetrievedMemory]
    degraded: bool = False


@router.get("/memories:retrieve", response_model=RetrieveResponse)
def retrieve_memories(
    query: Annotated[str, Query(min_length=1, max_length=10_000)],
    identity: Annotated[AuthContext, Depends(authenticated_identity)],
    encoder: Annotated[Encoder, Depends(get_encoder)],
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> RetrieveResponse:
    hits = retrieve_similar(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        query_embedding=encoder.encode(query),
        limit=limit,
    )
    return RetrieveResponse(
        memories=[
            RetrievedMemory(
                id=hit.memory.id,
                type=hit.memory.type,
                content=hit.memory.content,
                source_turn_ids=hit.memory.source_turn_ids,
                created_at=hit.memory.created_at,
                score=hit.score,
            )
            for hit in hits
        ]
    )
