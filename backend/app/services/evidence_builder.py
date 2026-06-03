from __future__ import annotations

from typing import Any

from app.services.llm_advisory import build_advisory_context


def build_evidence_context(prediction: dict[str, Any], date_key: str) -> dict[str, Any]:
    """Shared evidence payload for AI advisory, share pages and OG cards.

    The current implementation reuses the hardened advisory context so share and AI
    remain aligned on probability口径, ML gating, and meteorological hints.
    """
    ctx = build_advisory_context(prediction, date_key)
    hours = ctx.get("hourly_forecast_sunrise_window") or []
    top_factors: list[dict[str, Any]] = []
    pipeline = ctx.get("prediction_pipeline") or {}
    for item in pipeline.get("factor_snippets") or []:
        top_factors.append(item)
    ctx["top_factors"] = top_factors[:5]
    ctx["hourly_evidence_table"] = [
        {
            "time": h.get("time"),
            "cloudsea_pct": h.get("cloudsea_pct"),
            "sunrise_pct": h.get("sunrise_pct"),
            "temp_c": h.get("temp_c"),
            "rh_pct": h.get("rh_pct"),
            "cloud_low": h.get("cloud_low"),
            "cloud_mid": h.get("cloud_mid"),
            "vis_km": round(float(h.get("vis_m")) / 1000, 1) if h.get("vis_m") is not None else None,
            "precip_mm": h.get("precip_mm"),
        }
        for h in hours
    ]
    return ctx
