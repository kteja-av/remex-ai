"""entity column statistics for stable graph query plans

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-06
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE memory_entity_links ALTER COLUMN entity SET STATISTICS 1000")
    op.execute("ANALYZE memory_entity_links")


def downgrade() -> None:
    op.execute("ALTER TABLE memory_entity_links ALTER COLUMN entity SET STATISTICS -1")
