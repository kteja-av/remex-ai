import pytest
from alembic import command
from alembic.config import Config

from app.db.session import get_connection


def delete_tenant_memories(tenant_id: str) -> None:
    """Test teardown: bypass append-only triggers and RLS for the seeded tenant only."""
    with get_connection() as conn, conn.transaction():
        conn.execute("SET LOCAL row_security = off")
        conn.execute(
            "SELECT set_config('app.bypass_audit_immutability', 'on', true)"
        )
        conn.execute("DELETE FROM memory_audit WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            "DELETE FROM memory_entity_links WHERE tenant_id = %s", (tenant_id,)
        )
        conn.execute("DELETE FROM memories WHERE tenant_id = %s", (tenant_id,))


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> None:
    command.upgrade(Config("alembic.ini"), "head")
