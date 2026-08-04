from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps_auth import AuthContext, authenticated_identity
from app.audit.log import get_audit_trail
from app.domain.memory import AuditEvent

router = APIRouter(prefix="/v1", tags=["audit"])


class AuditEntryResponse(BaseModel):
    id: UUID
    event: AuditEvent
    actor: str
    source_turn_ids: list[UUID]
    created_at: datetime
    detail: dict | None = None


class AuditTrailResponse(BaseModel):
    memory_id: UUID
    events: list[AuditEntryResponse]


@router.get(
    "/memories/{memory_id}/audit",
    response_model=AuditTrailResponse,
)
def read_memory_audit(
    memory_id: UUID,
    identity: Annotated[AuthContext, Depends(authenticated_identity)],
) -> AuditTrailResponse:
    """Return the append-only trail explaining why this memory exists."""
    trail = get_audit_trail(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        memory_id=memory_id,
    )
    if trail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory not found")
    return AuditTrailResponse(
        memory_id=memory_id,
        events=[
            AuditEntryResponse(
                id=entry.id,
                event=entry.event,
                actor=entry.actor,
                source_turn_ids=entry.source_turn_ids,
                created_at=entry.created_at,
                detail=entry.detail,
            )
            for entry in trail
        ],
    )
