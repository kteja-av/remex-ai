from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.api.deps_auth import AuthContext, authenticated_identity
from app.domain.memory import MemoryStatus, MemoryType
from app.domain.policy import payload_from_request
from app.embedding.local_encoder import Encoder, get_encoder
from app.retrieval.vector import store_memory
from worker.queue import enqueue_evaluate, get_job_status, queue_has_capacity

router = APIRouter(prefix="/v1", tags=["memories"])


class CreateMemoryRequest(BaseModel):
    type: MemoryType
    content: str = Field(min_length=1, max_length=10_000)
    source_turn_ids: list[UUID] = Field(min_length=1)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class EvaluateMemoryRequest(BaseModel):
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


class EvaluateJobResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    result: dict | None = None
    error: str | None = None


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


@router.post(
    "/memories:evaluate",
    response_model=EvaluateJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def evaluate_memory(
    request: EvaluateMemoryRequest,
    identity: Annotated[AuthContext, Depends(authenticated_identity)],
) -> EvaluateJobResponse:
    """Enqueue a Write Gate evaluation job — returns immediately without blocking."""
    if not queue_has_capacity():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="write gate queue is at capacity",
        )
    payload = payload_from_request(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        memory_type=request.type,
        content=request.content,
        source_turn_ids=request.source_turn_ids,
        importance=request.importance,
    )
    job_id = enqueue_evaluate(payload)
    return EvaluateJobResponse(job_id=job_id)


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
)
def read_job_status(
    job_id: str,
    identity: Annotated[AuthContext, Depends(authenticated_identity)],
    response: Response,
) -> JobStatusResponse:
    response.headers["Cache-Control"] = "no-store"
    status_payload = get_job_status(job_id, str(identity.tenant_id))
    if status_payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return JobStatusResponse(
        job_id=status_payload["job_id"],
        status=status_payload["status"],
        result=status_payload.get("result"),
        error=status_payload.get("error"),
    )
