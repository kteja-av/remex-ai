from dataclasses import dataclass
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Json

from app.audit.log import record_audit_event
from app.db.models import MemoryRecord
from app.db.session import get_read_tenant_connection, get_tenant_connection
from app.domain.memory import AuditEvent, MemoryType
from app.embedding.local_encoder import EMBEDDING_DIMENSION
from app.retrieval.graph_links import index_entity_links


@dataclass(frozen=True)
class VectorHit:
    memory: MemoryRecord
    score: float


def _vector_literal(embedding: list[float]) -> str:
    if len(embedding) != EMBEDDING_DIMENSION:
        raise ValueError(
            f"embedding has {len(embedding)} dimensions; expected {EMBEDDING_DIMENSION}"
        )
    return "[" + ",".join(str(value) for value in embedding) + "]"


def store_memory(
    *,
    tenant_id: UUID,
    user_id: UUID,
    memory_type: MemoryType,
    content: str,
    source_turn_ids: list[UUID],
    embedding: list[float],
    importance: float = 0.5,
    write_gate_decision: dict | None = None,
    audit_actor: str = "direct_write",
) -> MemoryRecord:
    detail = (
        {"write_gate_trace": write_gate_decision} if write_gate_decision else None
    )
    with (
        get_tenant_connection(str(tenant_id)) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        row = cur.execute(
            """
            INSERT INTO memories (
                tenant_id, user_id, type, content, embedding,
                source_turn_ids, importance, write_gate_decision
            )
            VALUES (%s, %s, %s, %s, %s::vector, %s, %s, %s)
            RETURNING id, tenant_id, user_id, type, content, source_turn_ids,
                      created_at, updated_at, importance, decay_weight, status,
                      NULL::vector AS embedding, supersedes, write_gate_decision
            """,
            (
                tenant_id,
                user_id,
                memory_type.value,
                content,
                _vector_literal(embedding),
                source_turn_ids,
                importance,
                Json(write_gate_decision) if write_gate_decision else None,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("memory insert returned no row")
        record_audit_event(
            tenant_id=tenant_id,
            event=AuditEvent.ADMIT,
            actor=audit_actor,
            source_turn_ids=source_turn_ids,
            memory_id=row["id"],
            detail=detail,
            conn=conn,
        )
        index_entity_links(
            tenant_id=tenant_id,
            user_id=user_id,
            memory_id=row["id"],
            content=content,
            conn=conn,
        )
    return MemoryRecord.from_row(row)


def retrieve_similar(
    *,
    tenant_id: UUID,
    user_id: UUID,
    query_embedding: list[float],
    limit: int,
) -> list[VectorHit]:
    vector = _vector_literal(query_embedding)
    with (
        get_read_tenant_connection(str(tenant_id)) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        rows = cur.execute(
            """
            SELECT id, tenant_id, user_id, type, content, source_turn_ids,
                   created_at, updated_at, importance, decay_weight, status,
                   NULL::vector AS embedding, supersedes, write_gate_decision,
                   GREATEST(0.0, LEAST(1.0, 1.0 - (embedding <=> %s::vector)))
                       AS score
            FROM memories
            WHERE user_id = %s
              AND status = 'active'
              AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (vector, user_id, vector, limit),
        ).fetchall()
    return [
        VectorHit(memory=MemoryRecord.from_row(row), score=float(row["score"]))
        for row in rows
    ]
