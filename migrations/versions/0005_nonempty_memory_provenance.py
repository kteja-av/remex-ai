"""enforce nonempty memory provenance

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-31
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE memories
        ADD CONSTRAINT memories_source_turn_ids_nonempty
        CHECK (cardinality(source_turn_ids) > 0)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE memories
        DROP CONSTRAINT IF EXISTS memories_source_turn_ids_nonempty
        """
    )
