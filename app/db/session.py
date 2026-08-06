import psycopg

from app.config import settings

TENANT_GUC = "app.current_tenant"


def get_connection(connect_timeout: int = 2) -> psycopg.Connection:
    return psycopg.connect(settings.database_url, connect_timeout=connect_timeout)


def get_tenant_connection(
    tenant_id: str, connect_timeout: int = 2
) -> psycopg.Connection:
    """Write path and worker tenant connections — no read-path statement timeout."""
    conn = psycopg.connect(settings.app_database_url, connect_timeout=connect_timeout)
    try:
        conn.execute("SELECT set_config(%s, %s, true)", (TENANT_GUC, tenant_id))
        return conn
    except Exception:
        conn.close()
        raise


def get_read_tenant_connection(
    tenant_id: str, connect_timeout: int = 2
) -> psycopg.Connection:
    """Read-path tenant connections — bounded by READ_PATH_STATEMENT_TIMEOUT_MS."""
    conn = psycopg.connect(settings.app_database_url, connect_timeout=connect_timeout)
    try:
        conn.execute("SELECT set_config(%s, %s, true)", (TENANT_GUC, tenant_id))
        timeout_ms = settings.read_path_statement_timeout_ms
        if timeout_ms > 0:
            conn.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (f"{timeout_ms}ms",),
            )
        return conn
    except Exception:
        conn.close()
        raise
