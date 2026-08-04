"""Append-only audit log for memory mutations and Write Gate decisions."""

from typing import Any, Optional, cast
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Json

from app.db.models import AuditEventRecord
from app.db.session import get_tenant_connection
from app.domain.memory import AuditEvent


def record_audit_event(
    *,
    tenant_id: UUID,
    event: AuditEvent,
    actor: str,
    source_turn_ids: list[UUID],
    memory_id: UUID | None = None,
    detail: dict[str, Any] | None = None,
    conn: Any | None = None,
) -> AuditEventRecord:
    """Insert one immutable audit row. Optional conn must already carry the tenant GUC."""
    if conn is None:
        with (
            get_tenant_connection(str(tenant_id)) as owned_conn,
            owned_conn.cursor(row_factory=dict_row) as cur,
        ):
            row = _insert_audit_row(
                cur,
                tenant_id=tenant_id,
                event=event,
                actor=actor,
                source_turn_ids=source_turn_ids,
                memory_id=memory_id,
                detail=detail,
            )
        return AuditEventRecord.from_row(row)

    with conn.cursor(row_factory=dict_row) as cur:
        row = _insert_audit_row(
            cur,
            tenant_id=tenant_id,
            event=event,
            actor=actor,
            source_turn_ids=source_turn_ids,
            memory_id=memory_id,
            detail=detail,
        )
    return AuditEventRecord.from_row(row)


def _insert_audit_row(
    cur: Any,
    *,
    tenant_id: UUID,
    event: AuditEvent,
    actor: str,
    source_turn_ids: list[UUID],
    memory_id: UUID | None,
    detail: dict[str, Any] | None,
) -> dict[str, Any]:
    row = cur.execute(
        """
        INSERT INTO memory_audit (
            tenant_id, memory_id, event, actor, source_turn_ids, detail
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, tenant_id, memory_id, event, actor, source_turn_ids,
                  detail, created_at
        """,
        (
            tenant_id,
            memory_id,
            event.value,
            actor,
            source_turn_ids,
            Json(detail) if detail else None,
        ),
    ).fetchone()
    if row is None:
        raise RuntimeError("audit insert returned no row")
    return cast(dict[str, Any], row)


def get_audit_trail(
    *,
    tenant_id: UUID,
    user_id: UUID,
    memory_id: UUID,
) -> Optional[list[AuditEventRecord]]:
    with (
        get_tenant_connection(str(tenant_id)) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        memory = cur.execute(
            "SELECT id FROM memories WHERE id = %s AND user_id = %s",
            (memory_id, user_id),
        ).fetchone()
        if memory is None:
            return None

        rows = cur.execute(
            """
            SELECT id, tenant_id, memory_id, event, actor, source_turn_ids,
                   detail, created_at
            FROM memory_audit
            WHERE memory_id = %s
            ORDER BY created_at ASC, id ASC
            """,
            (memory_id,),
        ).fetchall()
    return [AuditEventRecord.from_row(row) for row in rows]
