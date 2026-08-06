import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RankingWeights:
    vector: float
    keyword: float
    graph: float
    relevance_exponent: float
    recency_exponent: float
    importance_exponent: float
    recency_half_life_days: float
    rrf_k: float
    candidate_multiplier: int


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
    ranking_weights: RankingWeights
    read_path_statement_timeout_ms: int

    @classmethod
    def from_env(cls) -> "Settings":
        ranking_weights = RankingWeights(
            vector=float(os.environ.get("RANKING_VECTOR_WEIGHT", "0.35")),
            keyword=float(os.environ.get("RANKING_KEYWORD_WEIGHT", "0.40")),
            graph=float(os.environ.get("RANKING_GRAPH_WEIGHT", "0.25")),
            relevance_exponent=float(
                os.environ.get("RANKING_RELEVANCE_EXPONENT", "1.0")
            ),
            recency_exponent=float(os.environ.get("RANKING_RECENCY_EXPONENT", "0.35")),
            importance_exponent=float(
                os.environ.get("RANKING_IMPORTANCE_EXPONENT", "0.65")
            ),
            recency_half_life_days=float(
                os.environ.get("RANKING_RECENCY_HALF_LIFE_DAYS", "30")
            ),
            rrf_k=float(os.environ.get("RANKING_RRF_K", "60")),
            candidate_multiplier=int(os.environ.get("RANKING_CANDIDATE_MULTIPLIER", "3")),
        )
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
            ranking_weights=ranking_weights,
            read_path_statement_timeout_ms=int(
                os.environ.get("READ_PATH_STATEMENT_TIMEOUT_MS", "150")
            ),
        )


settings = Settings.from_env()
