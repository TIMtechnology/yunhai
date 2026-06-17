#!/usr/bin/env python3
"""用高德 Web 服务校正观景点坐标（GCJ-02），以 Git 原始 WGS84 为锚点防误匹配。"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.engine.coord_transform import wgs84_to_gcj02  # noqa: E402

AMAP_KEY = "99ba25c09860105a1c0ea78aee2b9e7a"
SEARCH_URL = "https://restapi.amap.com/v3/place/text"
SLEEP_SEC = 0.12
MAX_SHIFT_KM = 18.0

# 过于泛化的观景点名：不能单独拿去搜 POI
_GENERIC_VP = re.compile(
    r"^(峰顶|主峰|山顶|顶|main|观景台|观景点|主观景点|入口|游客中心)$",
    re.I,
)


def _city_from_region(region: str) -> str:
    m = re.search(r"(北京|上海|天津|重庆|[^省]+?[市州盟])", region)
    return m.group(1) if m else ""


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _fetch(keywords: str, city: str = "", citylimit: bool = False) -> list[dict]:
    params: dict[str, str] = {
        "key": AMAP_KEY,
        "keywords": keywords,
        "offset": "10",
        "page": "1",
        "extensions": "base",
    }
    if city:
        params["city"] = city
        if citylimit:
            params["citylimit"] = "true"
    url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=20) as resp:
        data = json.loads(resp.read())
    if data.get("status") != "1":
        return []
    return data.get("pois") or []


def _spot_keywords(spot_name: str, aliases: list[str]) -> list[str]:
    keys = {spot_name}
    for a in aliases:
        if a and len(a) >= 2:
            keys.add(a)
    return [k for k in keys if len(k) >= 2]


def _name_matches_spot(text: str, spot_keys: list[str]) -> bool:
    return any(k in text for k in spot_keys)


def _score_poi(
    poi: dict,
    *,
    vp_name: str,
    spot_keys: list[str],
    anchor_lat: float,
    anchor_lng: float,
) -> int:
    name = poi.get("name") or ""
    address = poi.get("address") or ""
    text = f"{name} {address}"
    score = 0

    if not _name_matches_spot(text, spot_keys):
        score -= 200
    else:
        score += 100

    if vp_name and len(vp_name) >= 3 and vp_name in name:
        score += 60
    elif _GENERIC_VP.match(vp_name.strip()):
        score += 10
    elif vp_name in name or name in vp_name:
        score += 40

    if re.search(r"风景区|风景名胜区|景区|国家公园|森林公园|地质公园|山峰|雪山", text):
        score += 30
    if re.search(r"酒店|民宿|停车场|检查站|博物馆|公司|学校|医院|加油|商店", name):
        score -= 80
    # 明显不是同一座山的「峰顶」
    if _GENERIC_VP.match(vp_name.strip()) and not _name_matches_spot(name, spot_keys):
        score -= 150

    loc = poi.get("location") or ""
    if "," in loc:
        lng_s, lat_s = loc.split(",", 1)
        try:
            lat, lng = float(lat_s), float(lng_s)
            dist = _haversine_km(anchor_lat, anchor_lng, lat, lng)
            if dist <= 3:
                score += 40
            elif dist <= 8:
                score += 20
            elif dist <= MAX_SHIFT_KM:
                score += 5
            else:
                score -= int(min(120, dist))
        except ValueError:
            pass

    return score


def _parse_poi(poi: dict) -> tuple[float, float] | None:
    loc = poi.get("location") or ""
    if "," not in loc:
        return None
    lng_s, lat_s = loc.split(",", 1)
    try:
        return float(lat_s), float(lng_s)
    except ValueError:
        return None


def _build_queries(spot_name: str, region: str, vp_name: str, aliases: list[str]) -> list[str]:
    queries: list[str] = []
    generic = _GENERIC_VP.match(vp_name.strip())

    if generic:
        queries.extend([spot_name, f"{spot_name}风景区", f"{spot_name}景区"])
        for a in aliases:
            if a != spot_name:
                queries.append(a)
        if region:
            queries.append(f"{region}{spot_name}")
    else:
        if spot_name and vp_name and spot_name not in vp_name:
            queries.append(f"{spot_name}{vp_name}")
        queries.append(vp_name)
        if region:
            queries.append(f"{region}{spot_name}{vp_name}")
        queries.append(spot_name)

    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def geocode_viewpoint(
    spot_name: str,
    region: str,
    vp_name: str,
    aliases: list[str],
    anchor_lat: float,
    anchor_lng: float,
) -> tuple[float, float, str, str, str] | None:
    city = _city_from_region(region)
    spot_keys = _spot_keywords(spot_name, aliases)
    queries = _build_queries(spot_name, region, vp_name, aliases)

    best: tuple[float, float, str, str] | None = None
    best_score = -9999

    for q in queries:
        for citylimit in (True, False):
            pois = _fetch(q, city, citylimit=citylimit and bool(city))
            for poi in pois:
                pos = _parse_poi(poi)
                if not pos:
                    continue
                lat, lng = pos
                sc = _score_poi(
                    poi,
                    vp_name=vp_name,
                    spot_keys=spot_keys,
                    anchor_lat=anchor_lat,
                    anchor_lng=anchor_lng,
                )
                if sc > best_score:
                    best_score = sc
                    best = (lat, lng, poi.get("name") or q, q)
            if pois:
                break
        time.sleep(SLEEP_SEC)

    if not best or best_score < 30:
        return None

    lat, lng, poi_name, query = best
    dist = _haversine_km(anchor_lat, anchor_lng, lat, lng)
    if dist > MAX_SHIFT_KM and best_score < 80:
        return None
    return lat, lng, poi_name, query, "poi"


def _git_anchor(path: Path, vp_id: str) -> tuple[float, float, str] | None:
    rel = path.relative_to(ROOT)
    try:
        raw = subprocess.check_output(["git", "show", f"HEAD:{rel}"], cwd=ROOT, stderr=subprocess.DEVNULL)
        data = json.loads(raw)
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return None
    for vp in data.get("viewpoints") or []:
        if vp.get("id") == vp_id:
            lat, lng = vp.get("lat"), vp.get("lng")
            if lat is None or lng is None:
                return None
            sys_name = (data.get("coord_sys") or "WGS84").upper()
            if "GCJ" in sys_name:
                return float(lat), float(lng), "GCJ-02"
            gcj_lng, gcj_lat = wgs84_to_gcj02(float(lng), float(lat))
            return gcj_lat, gcj_lng, "anchor+WGS84→GCJ02"
    return None


def process_spot_file(path: Path) -> tuple[list[str], list[str], list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    spot_name = data.get("name") or path.stem
    region = data.get("region") or ""
    aliases = data.get("aliases") or []
    data["coord_sys"] = "GCJ-02"
    not_found: list[str] = []
    updated: list[str] = []
    kept_anchor: list[str] = []

    for vp in data.get("viewpoints") or []:
        vp_name = vp.get("name") or "main"
        vp_id = vp.get("id") or "main"
        label = f"{spot_name} · {vp_name}"
        old_lat, old_lng = vp.get("lat"), vp.get("lng")

        anchor = _git_anchor(path, vp_id)
        if anchor:
            anchor_lat, anchor_lng, anchor_src = anchor
        else:
            anchor_lat, anchor_lng, anchor_src = float(old_lat), float(old_lng), "current"

        result = geocode_viewpoint(spot_name, region, vp_name, aliases, anchor_lat, anchor_lng)
        if result:
            lat, lng, poi_name, query, src = result
            dist = _haversine_km(anchor_lat, anchor_lng, lat, lng)
            vp["lat"] = round(lat, 6)
            vp["lng"] = round(lng, 6)
            updated.append(
                f"{label}: ({old_lat},{old_lng}) → ({lat:.6f},{lng:.6f}) Δ{dist:.1f}km [{poi_name} / {query}]"
            )
        else:
            vp["lat"] = round(anchor_lat, 6)
            vp["lng"] = round(anchor_lng, 6)
            kept_anchor.append(f"{label}: 保留锚点 ({anchor_lat:.6f},{anchor_lng:.6f}) [{anchor_src}]")

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return updated, not_found, kept_anchor


def main() -> int:
    dirs = [
        ROOT / "data" / "scenic-spots",
        ROOT / "data" / "cloudsea" / "curated-spots",
    ]
    all_updated: list[str] = []
    all_kept: list[str] = []
    all_missing: list[str] = []

    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            if path.name.startswith("_"):
                continue
            updated, missing, kept = process_spot_file(path)
            all_updated.extend(updated)
            all_missing.extend(missing)
            all_kept.extend(kept)
            print(f"✓ {path.name}: {len(updated)} POI, {len(kept)} 锚点保留")

    report = ROOT / "data" / "scenic-spots" / "_amap_geocode_report.txt"
    lines = [
        f"POI 更新 {len(all_updated)} · 锚点保留 {len(all_kept)} · 未找到 {len(all_missing)}",
        "",
        "=== 锚点保留（POI 不可靠或未匹配） ===",
        *all_kept,
        "",
        "=== POI 更新 ===",
        *all_updated,
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n报告: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
