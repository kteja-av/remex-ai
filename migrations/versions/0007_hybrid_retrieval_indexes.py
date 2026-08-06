"""hybrid retrieval indexes: tsvector keyword index + entity-link graph table

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-05

ADR-0005: vector + keyword + entity-link graph signals for retrieval.
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE memories
        ADD COLUMN content_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
        """
    )
    op.execute(
        "CREATE INDEX memories_content_tsv_idx ON memories USING GIN (content_tsv)"
    )

    op.execute(
        """
        CREATE TABLE memory_entity_links (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            memory_id uuid NOT NULL REFERENCES memories (id) ON DELETE CASCADE,
            entity text NOT NULL CHECK (char_length(entity) > 0),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, user_id, memory_id, entity)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX memory_entity_links_lookup_idx
        ON memory_entity_links (tenant_id, user_id, entity)
        """
    )
    op.execute(
        """
        CREATE INDEX memory_entity_links_memory_idx
        ON memory_entity_links (tenant_id, memory_id)
        """
    )

    op.execute("ALTER TABLE memory_entity_links ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memory_entity_links FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY memory_entity_links_tenant_isolation ON memory_entity_links
        USING (tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid)
        """
    )
    op.execute("ANALYZE memory_entity_links")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_entity_links")
    op.execute("DROP INDEX IF EXISTS memories_content_tsv_idx")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS content_tsv")
