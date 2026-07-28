from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from app.config import curated_spots_dir, settings
from app.models.schemas import ScenicSpot, Viewpoint
from app.services.community_store import get_community_location
from app.services.spot_loader import reload_spots

_APP_ROOT = Path(__file__).resolve().parents[2]
_PROJECT_ROOT = (
    _APP_ROOT.parent if (_APP_ROOT.parent / "scripts").is_dir() else _APP_ROOT
)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", name.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:48] or "community-spot"


def _curated_spots_dir() -> Path:
    return curated_spots_dir()


def sync_curated_spot_from_location(location_id: str) -> None:
    """已落库精选的社区点：名称/坐标/海拔变更后同步 JSON。"""
    loc = get_community_location(location_id)
    if not loc or not loc.get("curated_spot_id"):
        return
    spot_id = loc["curated_spot_id"]
    if not str(spot_id).startswith("cs_"):
        return
    directory = _curated_spots_dir()
    target = directory / f"{spot_id}.json"
    if not target.exists():
        return
    viewpoint = Viewpoint(
        id="main",
        name=loc["name"],
        lat=loc["lat"],
        lng=loc["lng"],
        elevation=float(loc.get("elevation") or 0),
        tags=["sunrise", "cloudsea", "community"],
        note=f"社区贡献点位 · {location_id}",
    )
    spot = ScenicSpot(
        id=spot_id,
        name=loc["name"],
        aliases=[loc["name"]],
        region="社区精选",
        peak_elevation=float(loc.get("elevation") or 0),
        community_location_id=location_id,
        seasonality={
            "cloudsea_months": list(range(1, 13)),
            "sunrise_months": list(range(1, 13)),
            "sunrise_best": "all_year",
        },
        rules={"min_wind": 0.5, "max_wind": 12, "rh_threshold": 70},
        cloud_region={"span_lng": 1.5, "span_lat": 1.0},
        viewpoints=[viewpoint],
    )
    target.write_text(
        json.dumps(spot.model_dump(exclude_none=True), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    reload_spots()


def curate_community_location(location_id: str) -> dict[str, Any]:
    loc = get_community_location(location_id)
    if not loc:
        raise ValueError("社区点位未找到")
    preferred_id = loc["id"]
    target = _curated_spots_dir() / f"{preferred_id}.json"
    if loc.get("curated_spot_id") == preferred_id and target.exists():
        return {
            "spot_id": preferred_id,
            "file": str(target),
            "location_id": location_id,
            "already_curated": True,
        }
    curated_id = loc.get("curated_spot_id")
    if curated_id and curated_id != preferred_id and not str(curated_id).startswith("cs_"):
        return {
            "spot_id": curated_id,
            "location_id": location_id,
            "already_curated": True,
        }
    if int(loc.get("approved_label_count") or 0) < settings.cloudsea_curate_min_labels:
        raise ValueError(f"approved 标注不足 {settings.cloudsea_curate_min_labels} 天，暂不能精选落库")

    spot_id = preferred_id
    directory = _curated_spots_dir()
    target = directory / f"{spot_id}.json"
    old_curated = loc.get("curated_spot_id")
    if old_curated and old_curated != spot_id:
        old_file = directory / f"{old_curated}.json"
        if old_file.exists() and old_file != target:
            old_file.unlink(missing_ok=True)

    viewpoint = Viewpoint(
        id="main",
        name=loc["name"],
        lat=loc["lat"],
        lng=loc["lng"],
        elevation=float(loc.get("elevation") or 0),
        tags=["sunrise", "cloudsea", "community"],
        note=f"社区贡献点位 · {location_id}",
    )
    spot = ScenicSpot(
        id=spot_id,
        name=loc["name"],
        aliases=[loc["name"]],
        region="社区精选",
        peak_elevation=float(loc.get("elevation") or 0),
        community_location_id=location_id,
        seasonality={
            "cloudsea_months": list(range(1, 13)),
            "sunrise_months": list(range(1, 13)),
            "sunrise_best": "all_year",
        },
        rules={"min_wind": 0.5, "max_wind": 12, "rh_threshold": 70},
        cloud_region={"span_lng": 1.5, "span_lat": 1.0},
        viewpoints=[viewpoint],
    )
    target.write_text(
        json.dumps(spot.model_dump(exclude_none=True), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    reload_spots()

    from app.services.cloudsea_store import _connect, _now_iso

    with _connect() as conn:
        conn.execute(
            """
            UPDATE community_locations
            SET curated_spot_id=?, review_status='approved', updated_at=?
            WHERE id=?
            """,
            (spot_id, _now_iso(), location_id),
        )

    return {"spot_id": spot_id, "file": str(target), "location_id": location_id}


def maybe_auto_curate_location(location_id: str) -> Optional[dict[str, Any]]:
    loc = get_community_location(location_id)
    if not loc:
        return None
    if loc.get("curated_spot_id") == location_id:
        target = curated_spots_dir() / f"{location_id}.json"
        if target.exists():
            return None
    curated_id = loc.get("curated_spot_id")
    if curated_id and curated_id != location_id and not str(curated_id).startswith("cs_"):
        return None
    if int(loc.get("approved_label_count") or 0) < settings.cloudsea_curate_min_labels:
        return None
    try:
        return curate_community_location(location_id)
    except ValueError:
        return None


def run_model_training(
    *,
    db_path: Optional[str] = None,
    output_path: Optional[str] = None,
    spot_id: str = "wunvshan",
    viewpoint_id: str = "dianjiangtai",
) -> dict[str, Any]:
    db = db_path or settings.cloudsea_db_path
    db_parent = Path(db).resolve().parent
    safe_name = f"spot_{spot_id}_{viewpoint_id}".replace("/", "_")
    out = output_path or str(db_parent / "models" / "candidates" / f"{safe_name}.json")
    report = str(Path(out).with_suffix(".evaluation.json"))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    script = _PROJECT_ROOT / "scripts" / "train_operational_model.py"
    if not script.is_file():
        raise RuntimeError("受审训练脚本未包含在当前镜像中，不能生成候选模型")
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            db,
            "--spot-id",
            spot_id,
            "--viewpoint-id",
            viewpoint_id,
            "--output",
            out,
            "--report",
            report,
        ],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(_PROJECT_ROOT),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "训练失败")
    from app.engine.operational_ml import ArtifactValidationError, load_safe_artifact

    try:
        artifact = load_safe_artifact(
            Path(out),
            expected_spot_id=spot_id,
            expected_viewpoint_id=viewpoint_id,
        )
    except ArtifactValidationError as exc:
        raise RuntimeError(f"候选模型未通过安全与质量校验：{exc}") from exc
    evaluation = artifact["evaluation"]
    return {
        "status": "candidate_ready",
        "deploy_recommended": True,
        "reason": "候选已通过时间外评估；生产采用不可变镜像，需代码审查并随新版本发布",
        "holdout_balanced_accuracy": evaluation["metrics"]["balanced_accuracy"],
        "holdout_recall": evaluation["metrics"]["recall"],
        "holdout_brier": evaluation["metrics"]["brier"],
        "model_version": artifact["version"],
        "trained_days": artifact["n_days"],
        "output": out,
        "evaluation_report": report,
    }
