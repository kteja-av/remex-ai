import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_database_url: str
    redis_url: str
    worker_heartbeat_interval_seconds: float
    worker_heartbeat_stale_after_seconds: float
    write_gate_queue_name: str
    write_gate_max_queue_depth: int
    write_gate_sync: bool
    write_gate_provider_timeout_seconds: float
    nim_api_url: str | None
    nim_api_key: str | None
    gemini_api_url: str | None
    gemini_api_key: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get(
                "DATABASE_URL", "postgresql://cmis:cmis@postgres:5432/cmis"
            ),
            # Non-superuser role (migration 0003) used for every tenant-scoped
            # connection — superusers bypass RLS unconditionally.
            app_database_url=os.environ.get(
                "APP_DATABASE_URL", "postgresql://cmis_app:cmis_app@postgres:5432/cmis"
            ),
            redis_url=os.environ.get("REDIS_URL", "redis://redis:6379/0"),
            worker_heartbeat_interval_seconds=float(
                os.environ.get("WORKER_HEARTBEAT_INTERVAL_SECONDS", "5")
            ),
            worker_heartbeat_stale_after_seconds=float(
                os.environ.get("WORKER_HEARTBEAT_STALE_AFTER_SECONDS", "15")
            ),
            write_gate_queue_name=os.environ.get(
                "WRITE_GATE_QUEUE_NAME", "cmis:write_gate"
            ),
            write_gate_max_queue_depth=int(
                os.environ.get("WRITE_GATE_MAX_QUEUE_DEPTH", "1000")
            ),
            write_gate_sync=os.environ.get("WRITE_GATE_SYNC", "").lower()
            in {"1", "true", "yes"},
            write_gate_provider_timeout_seconds=float(
                os.environ.get("WRITE_GATE_PROVIDER_TIMEOUT_SECONDS", "30")
            ),
            nim_api_url=os.environ.get("NIM_API_URL"),
            nim_api_key=os.environ.get("NIM_API_KEY"),
            gemini_api_url=os.environ.get("GEMINI_API_URL"),
            gemini_api_key=os.environ.get("GEMINI_API_KEY"),
        )


settings = Settings.from_env()
