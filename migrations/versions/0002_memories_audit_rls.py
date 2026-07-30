"""typed memory schema + audit table + row-level security on every memory-bearing table

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-30

Design source: remex_ai_system_design_v1.md per-record fields (minimum) + ADR-0006.
RLS uses the per-connection GUC `app.current_tenant`; FORCE ROW LEVEL SECURITY so the
table owner (the app role today) cannot bypass it — seeding/migrations use
`SET row_security = off` explicitly instead.

"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

MEMORY_TYPES = ("episodic", "semantic", "procedural")
MEMORY_STATUSES = ("active", "archived", "deleted")
AUDIT_EVENTS = ("admit", "reject", "update", "supersede", "decay", "reflect", "archive", "delete")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE memories (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            type text NOT NULL CHECK (type IN ('episodic', 'semantic', 'procedural')),
            content text NOT NULL,
            embedding vector(384),
            source_turn_ids uuid[] NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            importance real NOT NULL DEFAULT 0.5 CHECK (importance >= 0.0 AND importance <= 1.0),
            decay_weight real NOT NULL DEFAULT 1.0 CHECK (decay_weight >= 0.0 AND decay_weight <= 1.0),
            status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived', 'deleted')),
            supersedes uuid REFERENCES memories (id),
            write_gate_decision jsonb
        )
        """
    )
    op.execute(
        "CREATE INDEX memories_tenant_user_status_idx ON memories (tenant_id, user_id, status)"
    )

    op.execute(
        """
        CREATE TABLE memory_audit (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            memory_id uuid REFERENCES memories (id),
            event text NOT NULL CHECK (event IN
                ('admit', 'reject', 'update', 'supersede', 'decay', 'reflect', 'archive', 'delete')),
            actor text NOT NULL,
            source_turn_ids uuid[] NOT NULL,
            detail jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX memory_audit_tenant_memory_idx ON memory_audit (tenant_id, memory_id)")

    for table in ("memories", "memory_audit"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid)
            """
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_audit")
    op.execute("DROP TABLE IF EXISTS memories")
