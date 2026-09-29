from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.routers.analytics import router as analytics_router
from app.routers.auth import router as auth_router
from app.routers.catalog import router as catalog_router
from app.routers.integrations import router as integrations_router
from app.routers.predictions import router as predictions_router
from app.routers.stream import router as stream_router
from app.routers.tickets import router as tickets_router
from app.routers.warehouse import router as warehouse_router

app = FastAPI(
    title="Москоллектор — прогноз инцидентов",
    version="0.7.0",
    description=(
        "Сервис поддержки диспетчера ОДС. "
        "Фаза 7: near-real-time СМВУ, калибровка порогов по feedback, сезонность."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-User-Login", "Content-Type"],
)
app.include_router(auth_router)
app.include_router(warehouse_router)
app.include_router(catalog_router)
app.include_router(predictions_router)
app.include_router(tickets_router)
app.include_router(analytics_router)
app.include_router(stream_router)
app.include_router(integrations_router)


@app.get("/health")
def health() -> dict:
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        db_ok = False
    data_dir = settings.data_dir
    return {
        "status": "ok" if db_ok else "degraded",
        "service": "moscollector-predict",
        "version": "0.7.0",
        "data_dir": str(data_dir),
        "data_dir_exists": data_dir.exists(),
        "database": "up" if db_ok else "down",
    }
