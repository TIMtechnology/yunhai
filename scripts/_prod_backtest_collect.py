#!/usr/bin/env python3
"""Collect backtest summaries from production internal API (run inside prod container)."""
import json
import os
import sys
import urllib.request

TOKEN = os.environ.get("CLOUDSEA_ADMIN_TOKEN", "")
BASE = os.environ.get("BACKTEST_BASE", "http://127.0.0.1:8088")


def fetch(spot: str, vp: str, date: str) -> dict:
    url = f"{BASE}/api/internal/backtest/predict?spot_id={spot}&viewpoint_id={vp}&date={date}"
    req = urllib.request.Request(url, headers={"X-Cloudsea-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode())


def summarize(bt: dict, date: str) -> dict:
    s = bt.get("sunrise_window_summary") or {}
    feat = s.get("features_at_peak") or {}
    loc = bt.get("prediction", {}).get("location", {})
    obs = loc.get("observable") or {}
    ml = loc.get("ml_status") or {}
    peak_h = None
    for h in bt.get("prediction", {}).get("hours", []):
        t = h.get("time", "")
        if t.startswith(date) and 3 <= int(t[11:13]) < 7:
            if peak_h is None or h["cloudsea"]["probability"] > peak_h["cloudsea"]["probability"]:
                peak_h = h
    vis = feat.get("visibility")
    return {
        "peak_prob": s.get("max_cloudsea_prob"),
        "scenario": s.get("scenario"),
        "data_source": bt.get("meta", {}).get("data_source"),
        "ml_active": ml.get("ml_active"),
        "ml_mode": ml.get("mode"),
        "cloud_low": feat.get("cloud_low"),
        "cloud_mid": feat.get("cloud_mid"),
        "cloud_high": feat.get("cloud_high"),
        "rh": feat.get("rh"),
        "rh_850": feat.get("rh_850"),
        "rh_700": feat.get("rh_700"),
        "inversion": feat.get("inversion"),
        "visibility_km": round(vis / 1000, 1) if vis else None,
        "precip48": feat.get("precip48"),
        "wind": feat.get("wind"),
        "obs_frac": obs.get("observable_fraction"),
        "sector_low": obs.get("sector_cloud_low_mean"),
        "archetype": peak_h.get("cloudsea", {}).get("archetype") if peak_h else None,
        "viewing_mode": loc.get("viewing_mode"),
        "peak_hour": peak_h.get("time") if peak_h else None,
    }


SPOTS = {
    ("donglingshan", "fengding"): [
        ("2025-08-17", "full"),
        ("2026-05-14", "full"),
        ("2026-05-17", "full"),
        ("2026-05-20", "partial"),
        ("2026-05-24", "full"),
        ("2026-05-28", "partial"),
        ("2026-05-29", "partial"),
        ("2025-08-24", "none"),
        ("2025-08-28", "none"),
        ("2026-05-21", "none"),
    ],
    ("wunvshan", "dianjiangtai"): [
        ("2026-05-04", "full"),
        ("2026-05-09", "full"),
        ("2026-05-20", "full"),
        ("2026-05-22", "full"),
        ("2026-05-29", "full"),
        ("2025-10-03", "full"),
        ("2025-10-14", "full"),
        ("2024-10-05", "full"),
        ("2026-05-28", "none"),
        ("2026-05-24", "none"),
        ("2026-05-25", "none"),
    ],
}


def main():
    if not TOKEN:
        print("CLOUDSEA_ADMIN_TOKEN required", file=sys.stderr)
        sys.exit(1)
    out = {"source": "production", "base": BASE, "spots": {}}
    for (spot, vp), days in SPOTS.items():
        rows = []
        for date, label in days:
            try:
                bt = fetch(spot, vp, date)
                row = {"date": date, "label": label, **summarize(bt, date)}
                rows.append(row)
            except Exception as e:
                rows.append({"date": date, "label": label, "error": str(e)})
        out["spots"][f"{spot}/{vp}"] = rows
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
