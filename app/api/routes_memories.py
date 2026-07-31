from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.api.deps_auth import AuthContext, authenticated_identity
from app.domain.memory import MemoryStatus, MemoryType
from app.embedding.local_encoder import Encoder, get_encoder
from app.retrieval.vector import store_memory

router = APIRouter(prefix="/v1", tags=["memories"])


class CreateMemoryRequest(BaseModel):
    type: MemoryType
    content: str = Field(min_length=1, max_length=10_000)
    source_turn_ids: list[UUID] = Field(min_length=1)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class MemoryResponse(BaseModel):
    id: UUID
    type: MemoryType
    content: str
    source_turn_ids: list[UUID]
    created_at: datetime
    importance: float
    status: MemoryStatus


@router.post(
    "/memories",
    response_model=MemoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_memory(
    request: CreateMemoryRequest,
    identity: Annotated[AuthContext, Depends(authenticated_identity)],
    encoder: Annotated[Encoder, Depends(get_encoder)],
) -> MemoryResponse:
    """M3 baseline only: directly persist every authenticated candidate."""
    record = store_memory(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        memory_type=request.type,
        content=request.content,
        source_turn_ids=request.source_turn_ids,
        importance=request.importance,
        embedding=encoder.encode(request.content),
    )
    return MemoryResponse(
        id=record.id,
        type=record.type,
        content=record.content,
        source_turn_ids=record.source_turn_ids,
        created_at=record.created_at,
        importance=record.importance,
        status=record.status,
    )
