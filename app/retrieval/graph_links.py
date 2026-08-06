from dataclasses import dataclass
from uuid import UUID

from psycopg.rows import dict_row

from app.db.models import MemoryRecord
from app.db.session import get_read_tenant_connection, get_tenant_connection
from app.retrieval.entities import extract_entities

ANCHOR_MEMORY_LIMIT = 20
BRIDGE_ENTITY_LIMIT = 20


@dataclass(frozen=True)
class GraphHit:
    memory: MemoryRecord
    score: float


def index_entity_links(
    *,
    tenant_id: UUID,
    user_id: UUID,
    memory_id: UUID,
    content: str,
    conn=None,
) -> list[str]:
    entities = extract_entities(content)
    if not entities:
        return []

    if conn is None:
        with get_tenant_connection(str(tenant_id)) as owned_conn:
            return index_entity_links(
                tenant_id=tenant_id,
                user_id=user_id,
                memory_id=memory_id,
                content=content,
                conn=owned_conn,
            )

    with conn.cursor() as cur:
        for entity in entities:
            cur.execute(
                """
                INSERT INTO memory_entity_links (
                    tenant_id, user_id, memory_id, entity
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (tenant_id, user_id, memory_id, entity) DO NOTHING
                """,
                (tenant_id, user_id, memory_id, entity),
            )
    return entities


def search_graph(
    *,
    tenant_id: UUID,
    user_id: UUID,
    query: str,
    limit: int,
) -> list[GraphHit]:
    entities = extract_entities(query)
    if not entities:
        return []

    with (
        get_read_tenant_connection(str(tenant_id)) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        direct_rows = cur.execute(
            """
            SELECT DISTINCT memory_id
            FROM memory_entity_links
            WHERE user_id = %s
              AND entity = ANY(%s)
            ORDER BY memory_id
            LIMIT %s
            """,
            (user_id, entities, ANCHOR_MEMORY_LIMIT),
        ).fetchall()
        direct_ids = [row["memory_id"] for row in direct_rows]
        if not direct_ids:
            return []

        bridge_rows = cur.execute(
            """
            SELECT DISTINCT entity
            FROM memory_entity_links
            WHERE user_id = %s
              AND memory_id = ANY(%s)
              AND NOT (entity = ANY(%s))
            ORDER BY entity
            LIMIT %s
            """,
            (user_id, direct_ids, entities, BRIDGE_ENTITY_LIMIT),
        ).fetchall()
        bridge_entities = [row["entity"] for row in bridge_rows]

        scores: dict[UUID, float] = {memory_id: 2.0 for memory_id in direct_ids}
        if bridge_entities:
            bridged_rows = cur.execute(
                """
                SELECT DISTINCT memory_id
                FROM memory_entity_links
                WHERE user_id = %s
                  AND entity = ANY(%s)
                  AND NOT (memory_id = ANY(%s))
                """,
                (user_id, bridge_entities, direct_ids),
            ).fetchall()
            for row in bridged_rows:
                scores.setdefault(row["memory_id"], 1.0)

        memory_ids = sorted(
            scores, key=lambda memory_id: scores[memory_id], reverse=True
        )[:limit]
        rows = cur.execute(
            """
            SELECT id, tenant_id, user_id, type, content, source_turn_ids,
                   created_at, updated_at, importance, decay_weight, status,
                   NULL::vector AS embedding, supersedes, write_gate_decision
            FROM memories
            WHERE id = ANY(%s)
              AND status = 'active'
            """,
            (memory_ids,),
        ).fetchall()

    hits = [
        GraphHit(memory=MemoryRecord.from_row(row), score=scores[row["id"]])
        for row in rows
    ]
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits[:limit]
