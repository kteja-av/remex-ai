from dataclasses import dataclass
from uuid import UUID

from psycopg.rows import dict_row

from app.db.models import MemoryRecord
from app.db.session import get_read_tenant_connection


@dataclass(frozen=True)
class KeywordHit:
    memory: MemoryRecord
    score: float


def search_keywords(
    *,
    tenant_id: UUID,
    user_id: UUID,
    query: str,
    limit: int,
) -> list[KeywordHit]:
    with (
        get_read_tenant_connection(str(tenant_id)) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        rows = cur.execute(
            """
            SELECT id, tenant_id, user_id, type, content, source_turn_ids,
                   created_at, updated_at, importance, decay_weight, status,
                   NULL::vector AS embedding, supersedes, write_gate_decision,
                   ts_rank_cd(content_tsv, websearch_to_tsquery('english', %s))
                       AS score
            FROM memories
            WHERE user_id = %s
              AND status = 'active'
              AND content_tsv @@ websearch_to_tsquery('english', %s)
            ORDER BY score DESC
            LIMIT %s
            """,
            (query, user_id, query, limit),
        ).fetchall()
    return [
        KeywordHit(memory=MemoryRecord.from_row(row), score=float(row["score"]))
        for row in rows
    ]
