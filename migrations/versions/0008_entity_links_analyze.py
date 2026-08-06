"""entity-link table planner stats + faster autovacuum analyze

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-06

L4 D1: cold statistics on memory_entity_links caused 60s+ graph queries.
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ANALYZE memory_entity_links")
    op.execute(
        """
        ALTER TABLE memory_entity_links SET (
            autovacuum_analyze_scale_factor = 0.02,
            autovacuum_analyze_threshold = 50
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE memory_entity_links RESET (
            autovacuum_analyze_scale_factor,
            autovacuum_analyze_threshold
        )
        """
    )
