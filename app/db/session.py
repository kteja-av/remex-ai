import psycopg

from app.config import settings


def get_connection(connect_timeout: int = 2) -> psycopg.Connection:
    return psycopg.connect(settings.database_url, connect_timeout=connect_timeout)
