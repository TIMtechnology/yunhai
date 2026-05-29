from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    tianditu_key: str = ""
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 1800
    scenic_spots_dir: str = str(_PROJECT_ROOT / "data" / "scenic-spots")
    static_dir: str = ""
    cors_origins: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    analytics_enabled: bool = False
    analytics_admin_token: str = ""
    analytics_retention_days: int = 90
    analytics_db_path: str = str(_PROJECT_ROOT / "data" / "analytics" / "analytics.db")
    cloudsea_enabled: bool = False
    cloudsea_admin_token: str = ""
    cloudsea_db_path: str = str(_PROJECT_ROOT / "data" / "cloudsea" / "cloudsea.db")
    cloudsea_auto_snapshot: bool = True
    cloudsea_ml_enabled: bool = False
    cloudsea_model_path: str = str(_PROJECT_ROOT / "data" / "cloudsea" / "models" / "cloudsea_ml_v2.pkl")
    cloudsea_contribute_enabled: bool = True
    cloudsea_daily_label_cap: int = 30
    cloudsea_max_locations_per_contributor: int = 10
    cloudsea_dedup_radius_m: float = 500.0
    cloudsea_auto_approve_trusted: bool = False
    cloudsea_community_auto_approve: bool = True
    cloudsea_curate_min_labels: int = 1
    cloudsea_curated_spots_dir: str = ""
    cloudsea_train_min_approved: int = 30
    cloudsea_ml_min_labels_per_spot: int = 30
    cloudsea_model_min_loocv: float = 0.70

    class Config:
        env_file = ".env"


settings = Settings()


def curated_spots_dir() -> Path:
    configured = settings.cloudsea_curated_spots_dir.strip()
    db_parent = Path(settings.cloudsea_db_path).resolve().parent
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = db_parent / path
    else:
        path = db_parent / "curated-spots"
    path.mkdir(parents=True, exist_ok=True)
    return path
