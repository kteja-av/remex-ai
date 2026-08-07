"""decay last_accessed_at + reflection derived-data table

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-07

M8: unused-age decay uses last_accessed_at; reflection summaries are derived rows
linked to their source memory ids (ON DELETE CASCADE on the summary memory).
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE memories
        ADD COLUMN last_accessed_at timestamptz NOT NULL DEFAULT now()
        """
    )
    op.execute(
        """
        UPDATE memories
        SET last_accessed_at = created_at
        WHERE last_accessed_at IS DISTINCT FROM created_at
        """
    )
    op.execute(
        """
        CREATE INDEX memories_decay_scan_idx
        ON memories (tenant_id, status, last_accessed_at)
        """
    )

    op.execute(
        """
        CREATE TABLE memory_reflections (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            summary_memory_id uuid NOT NULL
                REFERENCES memories (id) ON DELETE CASCADE,
            source_memory_ids uuid[] NOT NULL
                CHECK (cardinality(source_memory_ids) >= 2),
            source_fingerprint text NOT NULL
                CHECK (char_length(source_fingerprint) > 0),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, user_id, source_fingerprint)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX memory_reflections_summary_idx
        ON memory_reflections (tenant_id, summary_memory_id)
        """
    )
    op.execute(
        """
        CREATE INDEX memory_reflections_tenant_user_idx
        ON memory_reflections (tenant_id, user_id)
        """
    )

    op.execute("ALTER TABLE memory_reflections ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memory_reflections FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY memory_reflections_tenant_isolation ON memory_reflections
        USING (tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid)
        """
    )
    # DEFAULT PRIVILEGES from migration 0003 cover new tables owned by cmis;
    # grant explicitly so existing deployments stay consistent with memories/audit.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON memory_reflections TO cmis_app"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_reflections")
    op.execute("DROP INDEX IF EXISTS memories_decay_scan_idx")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS last_accessed_at")
