import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.rows import dict_row

from app.db.models import MemoryRecord
from app.db.session import get_connection, get_tenant_connection
from app.domain.memory import MemoryStatus, MemoryType


def _seed_memory(conn: psycopg.Connection, tenant_id: str, content: str) -> uuid.UUID:
    row = conn.execute(
        """
        INSERT INTO memories (tenant_id, user_id, type, content, source_turn_ids)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            tenant_id,
            str(uuid.uuid4()),
            MemoryType.SEMANTIC.value,
            content,
            [str(uuid.uuid4())],
        ),
    ).fetchone()
    assert row is not None
    return row[0]


@pytest.fixture()
def seeded() -> Iterator[dict[str, object]]:
    # Fresh tenants per test so reruns never see each other's rows. Seeding bypasses
    # RLS explicitly (owner role + row_security off), mirroring migrations/maintenance;
    # request-path code never does this. Teardown deletes only this test's rows.
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    with get_connection() as conn, conn.transaction():
        conn.execute("SET LOCAL row_security = off")
        ids = {
            "a1": _seed_memory(conn, tenant_a, "tenant A fact one"),
            "a2": _seed_memory(conn, tenant_a, "tenant A fact two"),
            "b1": _seed_memory(conn, tenant_b, "tenant B secret"),
        }
        conn.execute(
            """
            INSERT INTO memory_audit (tenant_id, memory_id, event, actor, source_turn_ids)
            VALUES (%s, %s, 'admit', 'test', %s)
            """,
            (tenant_b, ids["b1"], [str(uuid.uuid4())]),
        )
    yield {"tenant_a": tenant_a, "tenant_b": tenant_b, "ids": ids}
    with get_connection() as conn, conn.transaction():
        conn.execute("SET LOCAL row_security = off")
        conn.execute(
            "SELECT set_config('app.bypass_audit_immutability', 'on', true)"
        )
        conn.execute(
            "DELETE FROM memory_audit WHERE tenant_id = ANY(%s)", ([tenant_a, tenant_b],)
        )
        conn.execute(
            "DELETE FROM memory_entity_links WHERE tenant_id = ANY(%s)",
            ([tenant_a, tenant_b],),
        )
        conn.execute(
            "DELETE FROM memories WHERE tenant_id = ANY(%s)", ([tenant_a, tenant_b],)
        )


def _fetch_all(conn: psycopg.Connection, table: str) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608


def test_tenant_sees_only_own_memories(seeded: dict[str, object]) -> None:
    ids, tenant_a = seeded["ids"], seeded["tenant_a"]
    with get_tenant_connection(str(tenant_a)) as conn:
        rows = _fetch_all(conn, "memories")
    assert {r["id"] for r in rows} == {ids["a1"], ids["a2"]}
    assert all(r["tenant_id"] == uuid.UUID(str(tenant_a)) for r in rows)


def test_tenant_b_sees_only_own_memories(seeded: dict[str, object]) -> None:
    ids, tenant_b = seeded["ids"], seeded["tenant_b"]
    with get_tenant_connection(str(tenant_b)) as conn:
        rows = _fetch_all(conn, "memories")
    assert {r["id"] for r in rows} == {ids["b1"]}


def test_unscoped_select_returns_zero_rows(seeded: dict[str, object]) -> None:
    # Deliberately omits the tenant GUC — the RLS backstop must return nothing.
    with get_tenant_connection(str(uuid.uuid4())) as conn:
        conn.execute("SELECT set_config('app.current_tenant', '', false)")
        assert _fetch_all(conn, "memories") == []
        assert _fetch_all(conn, "memory_audit") == []


def test_audit_rows_are_tenant_scoped(seeded: dict[str, object]) -> None:
    ids = seeded["ids"]
    with get_tenant_connection(str(seeded["tenant_a"])) as conn:
        assert _fetch_all(conn, "memory_audit") == []
    with get_tenant_connection(str(seeded["tenant_b"])) as conn:
        rows = _fetch_all(conn, "memory_audit")
    assert len(rows) == 1
    assert rows[0]["memory_id"] == ids["b1"]


def test_cross_tenant_insert_is_rejected(seeded: dict[str, object]) -> None:
    # Tenant A's connection tries to write a row carrying tenant B's id: WITH CHECK blocks it.
    with (
        get_tenant_connection(str(seeded["tenant_a"])) as conn,
        pytest.raises(psycopg.Error),
    ):
        _seed_memory(conn, str(seeded["tenant_b"]), "forged tenant claim")


def test_memory_record_round_trips_through_schema(seeded: dict[str, object]) -> None:
    ids = seeded["ids"]
    with (
        get_tenant_connection(str(seeded["tenant_a"])) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        row = cur.execute("SELECT * FROM memories WHERE id = %s", (ids["a1"],)).fetchone()
    assert row is not None
    record = MemoryRecord.from_row(row)
    assert record.type is MemoryType.SEMANTIC
    assert record.status is MemoryStatus.ACTIVE
    assert record.importance == 0.5
    assert record.decay_weight == 1.0
    assert record.content == "tenant A fact one"


def test_provenance_columns_are_not_nullable(seeded: dict[str, object]) -> None:
    with (
        get_tenant_connection(str(seeded["tenant_a"])) as conn,
        pytest.raises(psycopg.Error),
    ):
        conn.execute(
            """
            INSERT INTO memories (tenant_id, user_id, type, content, source_turn_ids)
            VALUES (%s, %s, 'episodic', 'no provenance', NULL)
            """,
            (str(seeded["tenant_a"]), str(uuid.uuid4())),
        )
