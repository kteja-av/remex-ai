import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.domain.memory import AuditEvent, MemoryStatus, MemoryType, validate_weight


@dataclass(frozen=True)
class MemoryRecord:
    id: UUID
    tenant_id: UUID
    user_id: UUID
    type: MemoryType
    content: str
    source_turn_ids: list[UUID]
    created_at: datetime
    updated_at: datetime
    importance: float
    decay_weight: float
    status: MemoryStatus
    embedding: list[float] | None = None
    supersedes: UUID | None = None
    write_gate_decision: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        validate_weight("importance", self.importance)
        validate_weight("decay_weight", self.decay_weight)
        if not self.source_turn_ids:
            raise ValueError("source_turn_ids must be non-empty (no memory without provenance)")

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "MemoryRecord":
        decision = row["write_gate_decision"]
        if isinstance(decision, str):
            decision = json.loads(decision)
        return cls(
            id=row["id"],
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            type=MemoryType(row["type"]),
            content=row["content"],
            source_turn_ids=list(row["source_turn_ids"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            importance=row["importance"],
            decay_weight=row["decay_weight"],
            status=MemoryStatus(row["status"]),
            embedding=list(row["embedding"]) if row["embedding"] is not None else None,
            supersedes=row["supersedes"],
            write_gate_decision=decision,
        )


@dataclass(frozen=True)
class AuditEventRecord:
    id: UUID
    tenant_id: UUID
    event: AuditEvent
    actor: str
    source_turn_ids: list[UUID]
    created_at: datetime
    memory_id: UUID | None = None
    detail: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.source_turn_ids:
            raise ValueError("audit events must carry source turn ids (repudiation backstop)")

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "AuditEventRecord":
        detail = row["detail"]
        if isinstance(detail, str):
            detail = json.loads(detail)
        return cls(
            id=row["id"],
            tenant_id=row["tenant_id"],
            event=AuditEvent(row["event"]),
            actor=row["actor"],
            source_turn_ids=list(row["source_turn_ids"]),
            created_at=row["created_at"],
            memory_id=row["memory_id"],
            detail=detail,
        )
