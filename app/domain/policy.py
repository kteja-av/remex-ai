"""Write Gate policy types — admission verdicts and replayable decision traces."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from app.domain.memory import MemoryType


class AdmissionVerdict(StrEnum):
    ADMIT = "admit"
    REJECT = "reject"
    UPDATE = "update"


class PiiStatus(StrEnum):
    CLEAN = "clean"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class PiiVerdict:
    verdict_id: UUID
    status: PiiStatus
    matched_patterns: tuple[str, ...] = ()

    def to_trace(self) -> dict[str, Any]:
        return {
            "verdict_id": str(self.verdict_id),
            "status": self.status.value,
            "matched_patterns": list(self.matched_patterns),
        }


@dataclass(frozen=True)
class JudgeVerdict:
    verdict: AdmissionVerdict
    rationale: str
    provider: str
    importance: float = 0.5

    def to_trace(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "rationale": self.rationale,
            "provider": self.provider,
            "importance": self.importance,
        }


@dataclass(frozen=True)
class EvaluateCandidate:
    tenant_id: UUID
    user_id: UUID
    memory_type: MemoryType
    content: str
    source_turn_ids: list[UUID]
    importance: float = 0.5


def payload_from_request(
    *,
    tenant_id: UUID,
    user_id: UUID,
    memory_type: MemoryType,
    content: str,
    source_turn_ids: list[UUID],
    importance: float,
) -> dict[str, Any]:
    return {
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "type": memory_type.value,
        "content": content,
        "source_turn_ids": [str(value) for value in source_turn_ids],
        "importance": importance,
    }


@dataclass(frozen=True)
class WriteGateTrace:
    """Replayable decision trace stored on admitted rows and job results."""

    trace_id: UUID
    evaluated_at: datetime
    pii: PiiVerdict
    judge: JudgeVerdict | None
    actor: str = "write_gate"
    source_turn_ids: list[UUID] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": str(self.trace_id),
            "evaluated_at": self.evaluated_at.isoformat(),
            "actor": self.actor,
            "source_turn_ids": [str(value) for value in self.source_turn_ids],
            "pii_verdict": self.pii.to_trace(),
            "judge_verdict": self.judge.to_trace() if self.judge else None,
        }

    @classmethod
    def blocked_by_pii(
        cls,
        *,
        candidate: EvaluateCandidate,
        pii: PiiVerdict,
    ) -> "WriteGateTrace":
        return cls(
            trace_id=uuid4(),
            evaluated_at=datetime.now(UTC),
            pii=pii,
            judge=None,
            source_turn_ids=candidate.source_turn_ids,
        )

    @classmethod
    def from_judge(
        cls,
        *,
        candidate: EvaluateCandidate,
        pii: PiiVerdict,
        judge: JudgeVerdict,
    ) -> "WriteGateTrace":
        return cls(
            trace_id=uuid4(),
            evaluated_at=datetime.now(UTC),
            pii=pii,
            judge=judge,
            source_turn_ids=candidate.source_turn_ids,
        )
