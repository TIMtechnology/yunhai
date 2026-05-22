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

    class Config:
        env_file = ".env"


settings = Settings()
