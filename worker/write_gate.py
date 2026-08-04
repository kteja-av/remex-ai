"""Write Gate job processor — PII filter precedes any outbound judge call."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.audit.log import record_audit_event
from app.domain.memory import AuditEvent, MemoryType
from app.domain.policy import (
    AdmissionVerdict,
    EvaluateCandidate,
    PiiStatus,
    WriteGateTrace,
)
from app.embedding.local_encoder import get_encoder
from app.retrieval.vector import store_memory
from worker.llm_providers import judge_with_fallback
from worker.pii_filter import scan_text

# Test hook: when set, the judge sleeps before returning (never on the request path).
_judge_delay_seconds: float = 0.0


def set_judge_delay(seconds: float) -> None:
    global _judge_delay_seconds
    _judge_delay_seconds = seconds


def _parse_candidate(payload: dict[str, Any]) -> EvaluateCandidate:
    return EvaluateCandidate(
        tenant_id=UUID(payload["tenant_id"]),
        user_id=UUID(payload["user_id"]),
        memory_type=MemoryType(payload["type"]),
        content=payload["content"],
        source_turn_ids=[UUID(value) for value in payload["source_turn_ids"]],
        importance=float(payload.get("importance", 0.5)),
    )


def evaluate_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    candidate = _parse_candidate(payload)
    pii = scan_text(candidate.content)
    if pii.status is PiiStatus.BLOCKED:
        trace = WriteGateTrace.blocked_by_pii(candidate=candidate, pii=pii)
        record_audit_event(
            tenant_id=candidate.tenant_id,
            event=AuditEvent.REJECT,
            actor=trace.actor,
            source_turn_ids=candidate.source_turn_ids,
            detail={
                "outcome": "rejected",
                "reason": "pii_blocked",
                "write_gate_trace": trace.to_dict(),
            },
        )
        return {
            "outcome": "rejected",
            "reason": "pii_blocked",
            "trace": trace.to_dict(),
        }

    if _judge_delay_seconds > 0:
        import time

        time.sleep(_judge_delay_seconds)

    judge = judge_with_fallback(candidate.content)
    trace = WriteGateTrace.from_judge(candidate=candidate, pii=pii, judge=judge)

    if judge.verdict is not AdmissionVerdict.ADMIT:
        record_audit_event(
            tenant_id=candidate.tenant_id,
            event=AuditEvent.REJECT,
            actor=trace.actor,
            source_turn_ids=candidate.source_turn_ids,
            detail={
                "outcome": "rejected",
                "reason": "judge_reject",
                "write_gate_trace": trace.to_dict(),
            },
        )
        return {
            "outcome": "rejected",
            "reason": "judge_reject",
            "trace": trace.to_dict(),
        }

    encoder = get_encoder()
    trace_dict = trace.to_dict()
    record = store_memory(
        tenant_id=candidate.tenant_id,
        user_id=candidate.user_id,
        memory_type=candidate.memory_type,
        content=candidate.content,
        source_turn_ids=candidate.source_turn_ids,
        importance=judge.importance or candidate.importance,
        embedding=encoder.encode(candidate.content),
        write_gate_decision=trace_dict,
        audit_actor=trace.actor,
    )
    return {
        "outcome": "admitted",
        "memory_id": str(record.id),
        "trace": trace.to_dict(),
    }
