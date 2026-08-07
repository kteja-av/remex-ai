"""Reflection agent: consolidate related active memories into derived summaries.

Summaries are stored as normal memory rows plus a `memory_reflections` link row.
Idempotent via UNIQUE (tenant_id, user_id, source_fingerprint) — re-running with
the same source set does not create a second summary.
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Json

from app.audit.log import record_audit_event
from app.config import settings
from app.db.session import get_connection, get_tenant_connection
from app.domain.memory import AuditEvent, MemoryType
from app.embedding.local_encoder import EMBEDDING_DIMENSION, get_encoder
from app.retrieval.graph_links import index_entity_links


def _vector_literal(embedding: list[float]) -> str:
    if len(embedding) != EMBEDDING_DIMENSION:
        raise ValueError(
            f"embedding has {len(embedding)} dimensions; expected {EMBEDDING_DIMENSION}"
        )
    return "[" + ",".join(str(value) for value in embedding) + "]"

logger = logging.getLogger(__name__)

ACTOR = "reflection_agent"


def source_fingerprint(source_memory_ids: list[UUID]) -> str:
    joined = ",".join(str(memory_id) for memory_id in sorted(source_memory_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _list_tenant_user_pairs() -> list[tuple[UUID, UUID]]:
    with get_connection() as conn, conn.transaction():
        conn.execute("SET LOCAL row_security = off")
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                """
                SELECT DISTINCT tenant_id, user_id
                FROM memories
                WHERE status = 'active'
                ORDER BY tenant_id, user_id
                """
            ).fetchall()
    return [(row["tenant_id"], row["user_id"]) for row in rows]


def _connected_components(
    memory_ids: list[UUID], edges: list[tuple[UUID, UUID]]
) -> list[list[UUID]]:
    adjacency: dict[UUID, set[UUID]] = defaultdict(set)
    for left, right in edges:
        if left == right:
            continue
        adjacency[left].add(right)
        adjacency[right].add(left)

    seen: set[UUID] = set()
    components: list[list[UUID]] = []
    for memory_id in memory_ids:
        if memory_id in seen:
            continue
        stack = [memory_id]
        component: list[UUID] = []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.append(current)
            stack.extend(adjacency[current] - seen)
        components.append(sorted(component, key=str))
    return components


def _load_clusters(
    *, tenant_id: UUID, user_id: UUID
) -> list[list[dict[str, Any]]]:
    with (
        get_tenant_connection(str(tenant_id)) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        memories = cur.execute(
            """
            SELECT id, content, source_turn_ids, importance
            FROM memories
            WHERE user_id = %s
              AND status = 'active'
              AND COALESCE(write_gate_decision->>'kind', '') <> 'reflection'
            ORDER BY id
            """,
            (user_id,),
        ).fetchall()
        if len(memories) < settings.reflection_min_cluster_size:
            return []

        links = cur.execute(
            """
            SELECT memory_id, entity
            FROM memory_entity_links
            WHERE user_id = %s
            """,
            (user_id,),
        ).fetchall()

    by_id = {row["id"]: row for row in memories}
    entity_to_memories: dict[str, list[UUID]] = defaultdict(list)
    for link in links:
        memory_id = link["memory_id"]
        if memory_id in by_id:
            entity_to_memories[link["entity"]].append(memory_id)

    edges: list[tuple[UUID, UUID]] = []
    for members in entity_to_memories.values():
        unique = sorted(set(members), key=str)
        for index, left in enumerate(unique):
            for right in unique[index + 1 :]:
                edges.append((left, right))

    components = _connected_components(list(by_id.keys()), edges)
    clusters: list[list[dict[str, Any]]] = []
    for component in components:
        if len(component) < settings.reflection_min_cluster_size:
            continue
        clusters.append([by_id[memory_id] for memory_id in component])
    return clusters


def _build_summary_content(sources: list[dict[str, Any]]) -> str:
    snippets = [str(row["content"]).strip() for row in sources]
    joined = " | ".join(snippets)
    prefix = f"Reflection over {len(sources)} related memories: "
    # Keep under a conservative length for embedding + storage.
    max_body = 1800
    body = joined if len(joined) <= max_body else joined[: max_body - 1] + "…"
    return prefix + body


def _reflection_exists(
    *, tenant_id: UUID, user_id: UUID, fingerprint: str
) -> bool:
    with (
        get_tenant_connection(str(tenant_id)) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        row = cur.execute(
            """
            SELECT id FROM memory_reflections
            WHERE user_id = %s AND source_fingerprint = %s
            """,
            (user_id, fingerprint),
        ).fetchone()
    return row is not None


def _create_reflection(
    *,
    tenant_id: UUID,
    user_id: UUID,
    sources: list[dict[str, Any]],
) -> dict[str, Any] | None:
    source_ids = [row["id"] for row in sources]
    fingerprint = source_fingerprint(source_ids)
    if _reflection_exists(
        tenant_id=tenant_id, user_id=user_id, fingerprint=fingerprint
    ):
        return None

    content = _build_summary_content(sources)
    turn_ids: list[UUID] = []
    seen_turns: set[UUID] = set()
    for row in sources:
        for turn_id in row["source_turn_ids"]:
            if turn_id not in seen_turns:
                seen_turns.add(turn_id)
                turn_ids.append(turn_id)
    importance = max(float(row["importance"]) for row in sources)
    decision = {
        "kind": "reflection",
        "source_memory_ids": [str(memory_id) for memory_id in source_ids],
        "source_fingerprint": fingerprint,
        "actor": ACTOR,
    }
    encoder = get_encoder()
    embedding = encoder.encode(content)

    with get_tenant_connection(str(tenant_id)) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            memory_row = cur.execute(
                """
                INSERT INTO memories (
                    tenant_id, user_id, type, content, embedding,
                    source_turn_ids, importance, write_gate_decision
                )
                VALUES (%s, %s, %s, %s, %s::vector, %s, %s, %s)
                RETURNING id, source_turn_ids
                """,
                (
                    tenant_id,
                    user_id,
                    MemoryType.SEMANTIC.value,
                    content,
                    _vector_literal(embedding),
                    turn_ids,
                    importance,
                    Json(decision),
                ),
            ).fetchone()
            if memory_row is None:
                raise RuntimeError("reflection memory insert returned no row")

            summary_id = memory_row["id"]
            reflection_row = cur.execute(
                """
                INSERT INTO memory_reflections (
                    tenant_id, user_id, summary_memory_id,
                    source_memory_ids, source_fingerprint
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, user_id, source_fingerprint) DO NOTHING
                RETURNING id, summary_memory_id, source_memory_ids, source_fingerprint
                """,
                (tenant_id, user_id, summary_id, source_ids, fingerprint),
            ).fetchone()

            if reflection_row is None:
                # Lost the race / duplicate — roll back the orphan summary row.
                cur.execute("DELETE FROM memories WHERE id = %s", (summary_id,))
                return None

            record_audit_event(
                tenant_id=tenant_id,
                event=AuditEvent.REFLECT,
                actor=ACTOR,
                source_turn_ids=turn_ids,
                memory_id=summary_id,
                detail={
                    "source_memory_ids": [str(memory_id) for memory_id in source_ids],
                    "source_fingerprint": fingerprint,
                },
                conn=conn,
            )
            index_entity_links(
                tenant_id=tenant_id,
                user_id=user_id,
                memory_id=summary_id,
                content=content,
                conn=conn,
            )
        conn.commit()

    return {
        "summary_memory_id": str(summary_id),
        "source_memory_ids": [str(memory_id) for memory_id in source_ids],
        "source_fingerprint": fingerprint,
    }


def reflect_user(*, tenant_id: UUID, user_id: UUID) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    for cluster in _load_clusters(tenant_id=tenant_id, user_id=user_id):
        result = _create_reflection(
            tenant_id=tenant_id, user_id=user_id, sources=cluster
        )
        if result is not None:
            created.append(result)
    return created


def run_reflection_agent() -> dict[str, Any]:
    created = 0
    for tenant_id, user_id in _list_tenant_user_pairs():
        try:
            created += len(reflect_user(tenant_id=tenant_id, user_id=user_id))
        except Exception:
            logger.exception(
                "reflection failed for tenant=%s user=%s", tenant_id, user_id
            )
    return {
        "created": created,
        "ran_at": datetime.now(UTC).isoformat(),
    }
