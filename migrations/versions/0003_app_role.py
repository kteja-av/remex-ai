"""non-superuser application role for tenant-scoped connections

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-30

Root cause of the first RLS test failure: `cmis` (the POSTGRES_USER) is a superuser,
and superusers bypass row-level security unconditionally — even with FORCE. The
request path must connect as a non-superuser role (`cmis_app`) so the DB actually
enforces tenant isolation (ADR-0006). Migrations and seeding stay on the owner role.

"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Dev-stack credentials only; a real deployment injects the password via env/secret
    # and rotates it. The role must exist before any tenant-scoped connection is made.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cmis_app') THEN
                CREATE ROLE cmis_app LOGIN PASSWORD 'cmis_app';
            END IF;
        END
        $$
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO cmis_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON memories, memory_audit TO cmis_app")
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES FOR ROLE cmis IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cmis_app
        """
    )


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON memories, memory_audit FROM cmis_app")
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES FOR ROLE cmis IN SCHEMA public
        REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM cmis_app
        """
    )
    op.execute("REVOKE USAGE ON SCHEMA public FROM cmis_app")
    op.execute("DROP ROLE IF EXISTS cmis_app")
