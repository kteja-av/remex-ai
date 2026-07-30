from contextlib import asynccontextmanager
from typing import AsyncIterator

import psycopg
from fastapi import FastAPI

from app.api.routes_health import router as health_router
from app.db.session import get_connection


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        with get_connection() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.commit()
    except psycopg.Error:
        pass
    yield


app = FastAPI(title="remex-ai CMIS", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)
