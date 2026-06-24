from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    tianditu_key: str = ""
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 1800
    terrain_snapshots_dir: str = str(_PROJECT_ROOT / "data" / "terrain")
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
    cloudsea_watch_enabled: bool = True
    cloudsea_watch_label_days: int = 7
    cloudsea_watch_user_quiet_minutes: int = 60
    cloudsea_watch_rh_delta_pp: float = 5.0
    cloudsea_watch_cloud_low_delta_pp: float = 10.0

    # 大模型「当日出行解读」（OpenAI 兼容接口，如 DeepSeek / 通义 / OpenAI）
    llm_advisory_enabled: bool = False
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_advisory_cache_ttl: int = 86400
    llm_advisory_timeout_sec: float = 45.0

    # 分享页 / OG 图
    public_base_url: str = ""
    share_snapshot_ttl: int = 259200
    share_daily_ip_limit: int = 50
    share_assets_dir: str = str(_PROJECT_ROOT / "data" / "share-assets")
    share_cache_dir: str = str(_PROJECT_ROOT / "data" / "share-cache")
    apimart_enabled: bool = False
    apimart_api_key: str = ""
    share_og_use_image2: bool = True

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
