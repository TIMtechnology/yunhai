from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.middleware.analytics import AnalyticsMiddleware
from app.routers.analytics import router as analytics_router
from app.routers.cloudsea import router as cloudsea_router
from app.routers.contribute import router as contribute_router
from app.routers.advisory import router as advisory_router
from app.routers.share import router as share_router
from app.routers.api import router
from app.services.analytics_store import init_store, purge_expired
from app.services.cache import cache_ping, cache_status
from app.services.cloudsea_store import init_store as init_cloudsea_store
from app.services.spot_loader import load_spots
from app.services.terrain_store import preload_snapshots_to_cache

_docs_url = None if settings.analytics_enabled else "/docs"
_redoc_url = None if settings.analytics_enabled else "/redoc"
_openapi_url = None if settings.analytics_enabled else "/openapi.json"

app = FastAPI(
    title="云海日出预测 API",
    version="1.0.0",
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins + ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.analytics_enabled:
    app.add_middleware(AnalyticsMiddleware)

app.include_router(router)
app.include_router(advisory_router)
app.include_router(share_router)
app.include_router(analytics_router)
app.include_router(cloudsea_router)
app.include_router(contribute_router)

_startup_baked_count = 0


@app.on_event("startup")
async def startup():
    global _startup_baked_count
    load_spots()
    _startup_baked_count = preload_snapshots_to_cache()
    cache_ping()
    if settings.analytics_enabled:
        init_store()
        purge_expired()
    if settings.cloudsea_enabled:
        init_cloudsea_store()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "cache": cache_status(),
        "terrain_snapshots_preloaded": _startup_baked_count,
    }


_static_dir = settings.static_dir
if _static_dir and Path(_static_dir).is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
