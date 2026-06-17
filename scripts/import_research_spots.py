#!/usr/bin/env python3
"""将 _research-batch-*.json 合并、去重并写入 data/scenic-spots/{id}.json。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPOTS_DIR = ROOT / "data" / "scenic-spots"
sys.path.insert(0, str(ROOT / "backend"))

from app.models.schemas import ScenicSpot  # noqa: E402

BATCHES = [
    SPOTS_DIR / "_research" / "_research-batch-northeast-north-east.json",
    SPOTS_DIR / "_research" / "_research-batch-south-southwest-northwest.json",
]

SKIP_IDS = set()  # 已有精选 id 在此列出
CONF_RANK = {"high": 3, "medium": 2, "low": 1}


def _vp_score(vp: dict) -> int:
    return CONF_RANK.get(str(vp.get("coord_confidence") or "medium"), 2)


def _clean_viewpoint(vp: dict, fame_note: str) -> dict:
    out = {
        "id": vp["id"],
        "name": vp["name"],
        "lat": round(float(vp["lat"]), 5),
        "lng": round(float(vp["lng"]), 5),
        "elevation": round(float(vp["elevation"]), 1),
        "tags": list(vp.get("tags") or []),
        "note": str(vp.get("note") or "").strip(),
    }
    if vp.get("viewing_mode"):
        out["viewing_mode"] = vp["viewing_mode"]
    if not out["note"] and fame_note:
        out["note"] = fame_note[:120]
    return out


def _clean_spot(raw: dict) -> dict:
    fame = str(raw.pop("fame_note", "") or "")
    raw.pop("source", None)
    vps = raw.pop("viewpoints", [])
    cleaned_vps = [_clean_viewpoint(vp, fame) for vp in vps]
    rules = dict(raw.get("rules") or {})
    viewing_mode = rules.pop("viewing_mode", None)
    if viewing_mode and not any(vp.get("viewing_mode") for vp in cleaned_vps):
        for vp in cleaned_vps:
            vp.setdefault("viewing_mode", viewing_mode)
    out = {
        "id": raw["id"],
        "name": raw["name"],
        "aliases": list(raw.get("aliases") or []),
        "region": raw["region"],
        "peak_elevation": float(raw["peak_elevation"]),
        "coord_sys": raw.get("coord_sys") or "GCJ-02",
        "seasonality": dict(raw.get("seasonality") or {}),
        "rules": rules,
        "viewpoints": cleaned_vps,
        "source": "curated",
    }
    if raw.get("cloud_region"):
        out["cloud_region"] = raw["cloud_region"]
    if raw.get("local_water"):
        lw = dict(raw["local_water"])
        lw.pop("coord_confidence", None)
        out["local_water"] = lw
    return out


def _merge_spots(a: dict, b: dict) -> dict:
    """合并重复 id：保留置信度更高的 viewpoint，aliases 并集。"""
    merged = dict(a)
    merged["aliases"] = sorted(set(a.get("aliases", []) + b.get("aliases", [])))
    fame = a.get("_fame") or b.get("_fame", "")
    vp_by_id = {vp["id"]: vp for vp in a.get("viewpoints", [])}
    for vp in b.get("viewpoints", []):
        prev = vp_by_id.get(vp["id"])
        if prev is None or _vp_score(vp) > _vp_score(prev):
            vp_by_id[vp["id"]] = vp
    merged["viewpoints"] = list(vp_by_id.values())
    merged["_fame"] = fame or b.get("_fame", "")
    return merged


def load_merged() -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    for path in BATCHES:
        if not path.is_file():
            continue
        for item in json.loads(path.read_text(encoding="utf-8")):
            item["_fame"] = item.get("fame_note", "")
            sid = item["id"]
            if sid in by_id:
                by_id[sid] = _merge_spots(by_id[sid], item)
            else:
                by_id[sid] = item
    return by_id


def main() -> None:
    existing = {
        p.stem
        for p in SPOTS_DIR.glob("*.json")
        if not p.name.startswith("_")
    }
    merged = load_merged()
    written = 0
    skipped = 0
    errors: list[str] = []

    for sid, raw in sorted(merged.items()):
        if sid in existing or sid in SKIP_IDS:
            skipped += 1
            continue
        try:
            cleaned = _clean_spot(raw)
            ScenicSpot(**cleaned)  # validate
            out_path = SPOTS_DIR / f"{sid}.json"
            out_path.write_text(
                json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            written += 1
        except Exception as exc:
            errors.append(f"{sid}: {exc}")

    print(f"written={written} skipped={skipped} errors={len(errors)}")
    for e in errors[:20]:
        print("  ERR", e)


if __name__ == "__main__":
    main()
