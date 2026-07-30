import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    worker_heartbeat_interval_seconds: float
    worker_heartbeat_stale_after_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get(
                "DATABASE_URL", "postgresql://cmis:cmis@postgres:5432/cmis"
            ),
            redis_url=os.environ.get("REDIS_URL", "redis://redis:6379/0"),
            worker_heartbeat_interval_seconds=float(
                os.environ.get("WORKER_HEARTBEAT_INTERVAL_SECONDS", "5")
            ),
            worker_heartbeat_stale_after_seconds=float(
                os.environ.get("WORKER_HEARTBEAT_STALE_AFTER_SECONDS", "15")
            ),
        )


settings = Settings.from_env()
