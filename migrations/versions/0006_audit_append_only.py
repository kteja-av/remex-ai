"""append-only enforcement on memory_audit

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-04

Threat model §4: audit rows are immutable — revoke UPDATE/DELETE from the app role and
add BEFORE triggers so even the table owner cannot silently rewrite history through the
request path connection.

"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("REVOKE UPDATE, DELETE ON memory_audit FROM cmis_app")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_memory_audit_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF current_setting('app.bypass_audit_immutability', true) = 'on' THEN
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'memory_audit is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER memory_audit_no_update
        BEFORE UPDATE ON memory_audit
        FOR EACH ROW EXECUTE FUNCTION forbid_memory_audit_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER memory_audit_no_delete
        BEFORE DELETE ON memory_audit
        FOR EACH ROW EXECUTE FUNCTION forbid_memory_audit_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS memory_audit_no_delete ON memory_audit")
    op.execute("DROP TRIGGER IF EXISTS memory_audit_no_update ON memory_audit")
    op.execute("DROP FUNCTION IF EXISTS forbid_memory_audit_mutation()")
    op.execute("GRANT UPDATE, DELETE ON memory_audit TO cmis_app")
