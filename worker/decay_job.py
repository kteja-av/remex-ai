"""Background decay: absolute unused-age tiers → decay_weight + archive.

Idempotent: target weight is a pure function of days since last_accessed_at, so a
second run writes the same end state (no compounding). Off the request path only.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from app.audit.log import record_audit_event
from app.config import settings
from app.db.models import MemoryRecord
from app.db.session import get_connection, get_tenant_connection
from app.domain.memory import AuditEvent, MemoryStatus

logger = logging.getLogger(__name__)

ACTOR = "decay_job"


def target_decay_weight(days_unused: float) -> float:
    """Absolute tier schedule — re-running with the same age yields the same weight."""
    if days_unused < 30.0:
        return 1.0
    if days_unused < 60.0:
        return settings.decay_weight_after_30_days
    if days_unused < 90.0:
        return settings.decay_weight_after_60_days
    return settings.decay_weight_after_90_days


def _days_unused(last_accessed_at: datetime, *, now: datetime) -> float:
    if last_accessed_at.tzinfo is None:
        last_accessed_at = last_accessed_at.replace(tzinfo=UTC)
    return max((now - last_accessed_at).total_seconds() / 86_400.0, 0.0)


def _list_active_tenant_ids() -> list[UUID]:
    with get_connection() as conn, conn.transaction():
        conn.execute("SET LOCAL row_security = off")
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                """
                SELECT DISTINCT tenant_id
                FROM memories
                WHERE status = 'active'
                ORDER BY tenant_id
                """
            ).fetchall()
    return [row["tenant_id"] for row in rows]


def _load_active_memories(tenant_id: UUID) -> list[dict[str, Any]]:
    with (
        get_tenant_connection(str(tenant_id)) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        return cur.execute(
            """
            SELECT id, tenant_id, user_id, type, content, source_turn_ids,
                   created_at, updated_at, last_accessed_at, importance,
                   decay_weight, status, supersedes, write_gate_decision,
                   NULL::vector AS embedding
            FROM memories
            WHERE status = 'active'
            ORDER BY id
            """
        ).fetchall()


def _apply_decay_row(
    *,
    tenant_id: UUID,
    row: dict[str, Any],
    now: datetime,
) -> dict[str, Any] | None:
    days = _days_unused(row["last_accessed_at"], now=now)
    new_weight = target_decay_weight(days)
    should_archive = new_weight <= settings.decay_archive_threshold
    old_weight = float(row["decay_weight"])
    weight_changed = abs(old_weight - new_weight) > 1e-9

    if not weight_changed and not should_archive:
        return None

    with get_tenant_connection(str(tenant_id)) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            updated = cur.execute(
                """
                UPDATE memories
                SET decay_weight = %s,
                    status = CASE WHEN %s THEN 'archived' ELSE status END,
                    updated_at = %s
                WHERE id = %s
                  AND status = 'active'
                RETURNING id, tenant_id, user_id, type, content, source_turn_ids,
                          created_at, updated_at, importance, decay_weight, status,
                          NULL::vector AS embedding, supersedes, write_gate_decision
                """,
                (new_weight, should_archive, now, row["id"]),
            ).fetchone()
            if updated is None:
                return None

            if weight_changed:
                record_audit_event(
                    tenant_id=tenant_id,
                    event=AuditEvent.DECAY,
                    actor=ACTOR,
                    source_turn_ids=list(row["source_turn_ids"]),
                    memory_id=row["id"],
                    detail={
                        "days_unused": round(days, 4),
                        "decay_weight_before": old_weight,
                        "decay_weight_after": new_weight,
                    },
                    conn=conn,
                )
            if should_archive and updated["status"] == MemoryStatus.ARCHIVED.value:
                record_audit_event(
                    tenant_id=tenant_id,
                    event=AuditEvent.ARCHIVE,
                    actor=ACTOR,
                    source_turn_ids=list(row["source_turn_ids"]),
                    memory_id=row["id"],
                    detail={
                        "reason": "decay_below_threshold",
                        "decay_weight": new_weight,
                        "archive_threshold": settings.decay_archive_threshold,
                    },
                    conn=conn,
                )
        conn.commit()

    record = MemoryRecord.from_row(updated)
    return {
        "memory_id": str(record.id),
        "decay_weight": record.decay_weight,
        "status": record.status.value,
        "ranking_weight": record.importance * record.decay_weight,
        "days_unused": days,
        "archived": should_archive,
    }


def decay_tenant(tenant_id: UUID, *, now: datetime | None = None) -> list[dict[str, Any]]:
    clock = now or datetime.now(UTC)
    results: list[dict[str, Any]] = []
    for row in _load_active_memories(tenant_id):
        applied = _apply_decay_row(tenant_id=tenant_id, row=row, now=clock)
        if applied is not None:
            results.append(applied)
    return results


def run_decay_job(*, now: datetime | None = None) -> dict[str, Any]:
    clock = now or datetime.now(UTC)
    changed = 0
    archived = 0
    for tenant_id in _list_active_tenant_ids():
        try:
            results = decay_tenant(tenant_id, now=clock)
        except Exception:
            logger.exception("decay failed for tenant %s", tenant_id)
            continue
        changed += len(results)
        archived += sum(1 for item in results if item["archived"])
    return {"changed": changed, "archived": archived, "ran_at": clock.isoformat()}
