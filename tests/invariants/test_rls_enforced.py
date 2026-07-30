import pytest
from psycopg.rows import dict_row

from app.db.session import get_connection, get_tenant_connection

MEMORY_BEARING_TABLES = ("memories", "memory_audit")


def _tables() -> list[str]:
    # Discovered, not hardcoded alone: any future table whose name suggests memory
    # content must also be covered, or this invariant test fails.
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
              AND (tablename LIKE '%memor%' OR tablename LIKE '%audit%')
            """
        ).fetchall()
    discovered = {r["tablename"] for r in rows}
    return sorted(discovered | set(MEMORY_BEARING_TABLES))


def test_rls_enabled_and_forced_on_every_memory_bearing_table() -> None:
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            """
            SELECT relname, relrowsecurity, relforcerowsecurity
            FROM pg_class
            WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'
            """
        ).fetchall()
    by_name = {r["relname"]: r for r in rows}
    for table in _tables():
        assert table in by_name, f"{table} missing"
        assert by_name[table]["relrowsecurity"], f"{table}: RLS not enabled"
        assert by_name[table]["relforcerowsecurity"], f"{table}: RLS not forced (owner bypasses)"


def test_tenant_policy_exists_on_every_memory_bearing_table() -> None:
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "SELECT tablename, policyname, cmd FROM pg_policies WHERE schemaname = 'public'"
        ).fetchall()
    by_table: dict[str, list[dict]] = {}
    for r in rows:
        by_table.setdefault(r["tablename"], []).append(r)
    for table in _tables():
        policies = by_table.get(table, [])
        assert policies, f"{table}: no RLS policy"
        commands = {p["cmd"] for p in policies}
        # ALL covers SELECT/INSERT/UPDATE/DELETE; otherwise each verb needs its own policy
        assert "ALL" in commands or {"SELECT", "INSERT", "UPDATE", "DELETE"} <= commands, (
            f"{table}: policies do not cover all verbs: {commands}"
        )


@pytest.mark.parametrize("table", MEMORY_BEARING_TABLES)
def test_rls_blocks_forged_tenant_claim(table: str) -> None:
    # Spoofing probe from the threat model: a connection claiming tenant X must not
    # read rows belonging to tenant Y, with the policy as the only line of defense.
    import uuid

    victim, attacker = str(uuid.uuid4()), str(uuid.uuid4())
    with get_connection() as conn, conn.transaction():
        conn.execute("SET LOCAL row_security = off")
        if table == "memories":
            conn.execute(
                """
                INSERT INTO memories (tenant_id, user_id, type, content, source_turn_ids)
                VALUES (%s, %s, 'semantic', 'victim secret', %s)
                """,
                (victim, str(uuid.uuid4()), [str(uuid.uuid4())]),
            )
        else:
            conn.execute(
                """
                INSERT INTO memory_audit (tenant_id, event, actor, source_turn_ids)
                VALUES (%s, 'admit', 'test', %s)
                """,
                (victim, [str(uuid.uuid4())]),
            )
    with get_tenant_connection(attacker) as conn:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
    assert count is not None and count[0] == 0
    with get_connection() as conn, conn.transaction():
        conn.execute("SET LOCAL row_security = off")
        conn.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (victim,))  # noqa: S608
