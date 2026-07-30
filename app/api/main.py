from fastapi import FastAPI

from app.api.routes_health import router as health_router

# Schema (including the pgvector extension) is owned by Alembic migrations since M2.
# Run `alembic upgrade head` before serving; /v1/health reports pgvector:false on an
# unmigrated database instead of self-healing, so migration drift is visible.
app = FastAPI(title="remex-ai CMIS", version="0.2.0")
app.include_router(health_router)
