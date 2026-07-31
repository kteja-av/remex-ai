"""add cosine HNSW index for the M3 vector baseline

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-31
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX memories_embedding_hnsw_idx
        ON memories USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS memories_embedding_hnsw_idx")
