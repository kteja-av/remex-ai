import psycopg

from app.config import settings

TENANT_GUC = "app.current_tenant"


def get_connection(connect_timeout: int = 2) -> psycopg.Connection:
    return psycopg.connect(settings.database_url, connect_timeout=connect_timeout)


def get_tenant_connection(
    tenant_id: str, connect_timeout: int = 2
) -> psycopg.Connection:
    conn = psycopg.connect(settings.app_database_url, connect_timeout=connect_timeout)
    conn.execute(
        "SELECT set_config(%s, %s, false)", (TENANT_GUC, tenant_id)
    )
    return conn
