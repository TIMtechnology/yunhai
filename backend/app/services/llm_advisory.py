"""基于结构化预报 + ML 结果，调用大模型生成「当日出行解读」（仅辅助说明）。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date as date_cls, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import settings
from app.engine.cloudsea_ml import get_ml_status
from app.services.cache import cache_get, cache_set

log = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")

SYSTEM_PROMPT = """你是高山日出云海观赏的气象与预测解读顾问。只能依据 JSON 中的数值发言，不得编造观测、模型版本或 LOOCV。

输出简体中文 Markdown，固定小节（不要增删）：
## 一句话结论
## 逐时气象解读（03:00–07:00）
## 气象机理简析
## 规则引擎与 ML 对照
## 出行建议
## 免责声明

硬性规则：
1. **判断倾向**：全文仅使用 `verdict_hint.verdict`；「一句话结论」与「出行建议」首条须一致。
2. **云海概率**：仅以 `ui_display_scores.cloudsea_prob_pct` 与 `hourly_forecast_sunrise_window` 为准；**禁止**用 `day_summary.peak_cloudsea_prob`（全天峰值）。
3. **能见度**：`vis_m` 为米，写作 X.X km。
4. **逐时气象**：逐小时引用数据；可用 Markdown 表格。
5. **气象机理简析**：结合 `meteo_analysis_hints` 与逐时数据，说明湿度/云量/能见度/前日降水与「能否成云海」的因果（支持或质疑当日偏低/偏高概率），勿空泛套话。
6. **规则引擎与 ML 对照**（核心）：
   - 必读 `prediction_pipeline`。若 `ml_active` 为 false：写明「本点位未启用 ML」，**只解读** `rule_engine_cloudsea_pct` 与融合展示分，**禁止**编造 P_ml、LOOCV、模型名、标注天数。
   - 若 `ml_active` 为 true：写出规则%、ML 原始%、融合% 三者；明确写「佐证」或「驳斥」——当气象机理与某一方矛盾时说明理由（例如：高湿低云量却两者均给低分→一致偏保守；规则明显高于 ML→指出规则可能高估）。
   - 可引用 `ml_calibration` 的 LOOCV 说明可信度，但不得夸大。
7. **免责声明**：仅一句「AI 辅助解读，数值预测以系统为准，观赏受天气突变影响。」
8. 不要输出 JSON；约 600–900 字。"""


def _hour_rows_for_date(hours: list[dict], date_key: str) -> list[dict]:
    rows = [h for h in hours if str(h.get("time", "")).startswith(date_key)]
    return sorted(rows, key=lambda h: h.get("time", ""))


def _precip_prev_day(hours: list[dict], date_key: str) -> float | None:
    try:
        prev = (date_cls.fromisoformat(date_key) - timedelta(days=1)).isoformat()
    except ValueError:
        return None
    prev_hours = _hour_rows_for_date(hours, prev)
    if not prev_hours:
        return None
    return round(sum(float((h.get("weather") or {}).get("precipitation") or 0) for h in prev_hours), 2)


def _hourly_row(h: dict) -> dict[str, Any]:
    t = str(h.get("time", ""))
    w = h.get("weather") or {}
    cs = h.get("cloudsea") if isinstance(h.get("cloudsea"), dict) else {}
    sr = h.get("sunrise") if isinstance(h.get("sunrise"), dict) else {}
    sc = h.get("scenario") if isinstance(h.get("scenario"), dict) else {}
    return {
        "time": t[11:16] if "T" in t else t,
        "is_sunrise_window": bool(h.get("is_sunrise_window")),
        "temp_c": w.get("temperature"),
        "rh_pct": w.get("humidity"),
        "precip_mm": w.get("precipitation"),
        "cloud_low": w.get("cloud_cover_low"),
        "cloud_mid": w.get("cloud_cover_mid"),
        "cloud_high": w.get("cloud_cover_high"),
        "vis_m": w.get("visibility"),
        "wind_ms": w.get("wind_speed"),
        "weather_text": w.get("weather_text"),
        "cloudsea_pct": cs.get("probability"),
        "cloudsea_grade": cs.get("grade"),
        "sunrise_pct": sr.get("probability"),
        "sunrise_grade": sr.get("grade"),
        "scenario": sc.get("label"),
        "combined_score": sc.get("combined_score"),
    }


def _sunrise_window_table(hours: list[dict], date_key: str) -> list[dict]:
    out: list[dict] = []
    for h in _hour_rows_for_date(hours, date_key):
        t = str(h.get("time", ""))
        if "T" not in t:
            continue
        hour = int(t[11:13])
        if hour < 3 or hour >= 7:
            continue
        out.append(_hourly_row(h))
    return out


def _sunrise_window_stats(rows: list[dict]) -> dict[str, Any]:
    if not rows:
        return {}
    vis = [(r["time"], float(r["vis_m"])) for r in rows if r.get("vis_m") is not None]
    cs = [int(r["cloudsea_pct"]) for r in rows if r.get("cloudsea_pct") is not None]
    stats: dict[str, Any] = {
        "hours_count": len(rows),
        "cloudsea_min_pct": min(cs) if cs else None,
        "cloudsea_max_pct": max(cs) if cs else None,
    }
    if vis:
        lo = min(vis, key=lambda x: x[1])
        hi = max(vis, key=lambda x: x[1])
        stats["visibility_min_m"] = round(lo[1], 0)
        stats["visibility_min_at"] = lo[0]
        stats["visibility_max_m"] = round(hi[1], 0)
    return stats


def _ui_display_scores(hours: list[dict], day_summary: dict[str, Any] | None) -> dict[str, Any]:
    """与主页 ScenarioHero 一致：日出窗口云海峰值小时的综合分（非天文日出整点）。"""
    if not day_summary:
        return {}
    idx = day_summary.get("sunrise_window_peak_hour_index")
    if idx is None:
        idx = day_summary.get("sunrise_hour_index")
    if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(hours):
        return {}
    h = hours[idx]
    cs = h.get("cloudsea") if isinstance(h.get("cloudsea"), dict) else {}
    sr = h.get("sunrise") if isinstance(h.get("sunrise"), dict) else {}
    sc = h.get("scenario") if isinstance(h.get("scenario"), dict) else {}
    t = str(h.get("time", ""))
    return {
        "time": t[11:16] if "T" in t else t,
        "cloudsea_prob_pct": cs.get("probability"),
        "cloudsea_grade": cs.get("grade"),
        "sunrise_prob_pct": sr.get("probability"),
        "sunrise_grade": sr.get("grade"),
        "combined_score": sc.get("combined_score"),
        "scenario_label": sc.get("label"),
        "matches_main_panel": True,
    }


def _compute_verdict_hint(
    day_summary: dict[str, Any] | None,
    window_rows: list[dict],
    ui_scores: dict[str, Any],
) -> dict[str, Any]:
    """与主页卡片一致：以日出时刻/日出窗口云海概率为准，不用全天峰值。"""
    stats = _sunrise_window_stats(window_rows)
    window_peak = int(stats.get("cloudsea_max_pct") or 0)
    ui_cs = int(ui_scores.get("cloudsea_prob_pct") or 0)
    # 与界面一致：优先日出时刻，其次 03–07 窗口内最大
    cloudsea_for_verdict = ui_cs if ui_cs else window_peak

    combined = int(ui_scores.get("combined_score") or (day_summary or {}).get("sunrise_combined_score") or 0)
    sunrise_prob = int(ui_scores.get("sunrise_prob_pct") or 0)
    scenario = ui_scores.get("scenario_label") or (day_summary or {}).get("sunrise_scenario_label") or ""

    if cloudsea_for_verdict < 20:
        verdict = "不建议"
        rationale = f"日出时段云海约 {cloudsea_for_verdict}%（偏低），不宜为云海专程。"
    elif cloudsea_for_verdict < 35:
        verdict = "谨慎前往"
        rationale = (
            f"日出时段云海约 {cloudsea_for_verdict}%（较差），若以云海为主需降低预期；"
            f"可兼顾日出（综合 {combined}、日出概率 {sunrise_prob}%）。"
        )
    elif cloudsea_for_verdict < 55:
        verdict = "值得观望"
        rationale = f"日出时段云海约 {cloudsea_for_verdict}%（中等），建议结合实时云况再决定。"
    else:
        verdict = "推荐前往"
        rationale = f"日出时段云海约 {cloudsea_for_verdict}%（较好），值得安排凌晨行程。"

    if sunrise_prob >= 65 and combined >= 65 and cloudsea_for_verdict < 35:
        # 日出好、云海差：不升级为推荐，最多维持谨慎
        if verdict == "不建议":
            verdict = "谨慎前往"
            rationale += " 日出条件较好，可仅为看日出前往，勿高估云海。"

    stats_vis = stats.get("visibility_min_m")
    if stats_vis is not None and stats_vis < 3000:
        rationale += (
            f" {stats.get('visibility_min_at')} 能见度约 {stats_vis / 1000:.1f} km 偏低，注意行车安全。"
        )

    day_full_peak = int((day_summary or {}).get("peak_cloudsea_prob") or 0)
    day_peak_time = (day_summary or {}).get("peak_cloudsea_time")

    return {
        "verdict": verdict,
        "cloudsea_prob_pct_for_advice": cloudsea_for_verdict,
        "sunrise_window_cloudsea_max_pct": window_peak,
        "sunrise_combined_score": combined,
        "sunrise_prob_pct": sunrise_prob,
        "sunrise_scenario_label": scenario,
        "rationale": rationale,
        "do_not_use_day_peak": {
            "peak_cloudsea_prob_pct": day_full_peak,
            "peak_time": day_peak_time,
            "reason": "此为全天24小时规则引擎峰值，非日出窗口，勿写入结论",
        },
    }


def _parse_pct_from_factor(fd: dict[str, Any] | None) -> int | None:
    if not fd or not isinstance(fd, dict):
        return None
    val = str(fd.get("value") or "")
    m = re.search(r"(\d+)\s*%", val)
    if m:
        return int(m.group(1))
    m = re.search(r"P_ml=(\d+)", val)
    if m:
        return int(m.group(1))
    return None


def _sunrise_hour_dict(
    hours: list[dict],
    day_summary: dict[str, Any] | None,
    date_key: str,
) -> dict[str, Any] | None:
    if day_summary:
        idx = day_summary.get("sunrise_hour_index")
        if idx is not None and isinstance(idx, int) and 0 <= idx < len(hours):
            return hours[idx]
    for h in _hour_rows_for_date(hours, date_key):
        if h.get("is_sunrise_window"):
            return h
    return None


def _build_prediction_pipeline(
    hours: list[dict],
    day_summary: dict[str, Any] | None,
    ml_status: dict[str, Any],
    ui_scores: dict[str, Any],
    date_key: str,
) -> dict[str, Any]:
    """规则 / ML / 融合拆解，供大模型佐证或驳斥。"""
    ml_active = bool(ml_status.get("ml_active"))
    mode = str(ml_status.get("mode") or "rule_only")
    h = _sunrise_hour_dict(hours, day_summary, date_key)
    factors = (h.get("cloudsea") or {}).get("factors") if h else {}
    if not isinstance(factors, dict):
        factors = {}

    rule_pct = _parse_pct_from_factor(factors.get("fuzzy_reference"))
    ml_pct = _parse_pct_from_factor(factors.get("ml_model")) if ml_active else None
    fused_pct = int(ui_scores.get("cloudsea_prob_pct") or (h.get("cloudsea") or {}).get("probability") or 0)

    pipeline: dict[str, Any] = {
        "ml_active": ml_active,
        "mode": mode,
        "ml_status_message": ml_status.get("message"),
        "rule_engine_cloudsea_pct": rule_pct,
        "ml_raw_cloudsea_pct": ml_pct,
        "fused_display_cloudsea_pct": fused_pct,
        "eligible_labels": ml_status.get("eligible_labels"),
        "min_labels_for_ml": ml_status.get("min_labels"),
    }

    if ml_active and rule_pct is not None and ml_pct is not None:
        diff = ml_pct - rule_pct
        pipeline["rule_vs_ml_gap_pct"] = diff
        if abs(diff) <= 8:
            pipeline["agreement_hint"] = "规则与 ML 接近，气象上可视为互相佐证"
        elif diff < -10:
            pipeline["agreement_hint"] = "ML 明显低于规则，宜结合气象检验规则是否偏乐观"
        else:
            pipeline["agreement_hint"] = "ML 高于规则，宜检验是否低估湿度/地形信号"
        if fused_pct is not None:
            pipeline["fusion_note"] = f"页面展示 {fused_pct}% 为二者加权融合结果，非单独一方"
    elif not ml_active:
        pipeline["instruction"] = (
            "本点位未接入 ML（或标注未达标/模型未部署）。"
            "解读时仅使用规则引擎与气象，禁止引用 P_ml、LOOCV、专属模型版本。"
        )
        if rule_pct is not None and fused_pct is not None and rule_pct != fused_pct:
            pipeline["fusion_note"] = f"展示分 {fused_pct}% 可能含观测场上限等后处理，规则参考 {rule_pct}%"
        elif rule_pct is not None:
            pipeline["fusion_note"] = f"展示分与规则引擎一致或仅规则引擎生效（约 {rule_pct}%）"

    top_factors: list[dict[str, str]] = []
    for key in (
        "ml_model",
        "ml_factor_1",
        "ml_factor_2",
        "fuzzy_reference",
        "obs_pattern",
        "obs_cloud_layers",
        "obs_visibility_raw",
    ):
        fd = factors.get(key)
        if fd and isinstance(fd, dict) and fd.get("label"):
            top_factors.append(
                {
                    "key": key,
                    "label": str(fd.get("label")),
                    "value": str(fd.get("value") or fd.get("description") or ""),
                }
            )
    if not ml_active:
        top_factors = [f for f in top_factors if not f["key"].startswith("ml")]
    pipeline["factor_snippets"] = top_factors[:8]
    return pipeline


def _build_meteo_analysis_hints(
    window_rows: list[dict],
    precip_prev: float | None,
    loc: dict[str, Any],
) -> list[str]:
    """由气象字段推导的机理线索，供大模型展开（非结论替代）。"""
    hints: list[str] = []
    if not window_rows:
        return hints

    rh_vals = [float(r["rh_pct"]) for r in window_rows if r.get("rh_pct") is not None]
    low_cloud = [float(r["cloud_low"]) for r in window_rows if r.get("cloud_low") is not None]
    cs_vals = [int(r["cloudsea_pct"]) for r in window_rows if r.get("cloudsea_pct") is not None]

    if rh_vals and sum(rh_vals) / len(rh_vals) >= 82:
        hints.append("日出窗口平均相对湿度偏高（≥82%），近地面易雾，但低云量若持续偏薄则不利于典型山谷云海填充。")
    if low_cloud and max(low_cloud) <= 15:
        hints.append("低云量整体偏少（≤15%），不利于形成厚云毯，与偏低云海概率在机理上一致。")
    if precip_prev is not None:
        if precip_prev >= 1:
            hints.append(f"前一日降水 {precip_prev} mm，土壤与近地面湿度偏高，略利好清晨雾凇/云海，但需有合适风场与逆温配合。")
        else:
            hints.append("前一日无降水，水汽补给偏弱，不利于云海发展（机理上略压制概率）。")

    vis_rows = [(r["time"], float(r["vis_m"])) for r in window_rows if r.get("vis_m") is not None]
    if len(vis_rows) >= 2:
        lo = min(vis_rows, key=lambda x: x[1])
        hi = max(vis_rows, key=lambda x: x[1])
        if hi[1] > 0 and lo[1] < hi[1] * 0.5:
            hints.append(
                f"{lo[0]} 能见度约 {lo[1] / 1000:.1f} km 为窗口内最低，"
                "可能对应雾或近地面湿层，需与云海形态区分（雾≠一定有观赏级云海）。"
            )

    viewing = loc.get("viewing_mode")
    if viewing == "valley_fill":
        hints.append("观云模式为山谷填云：需谷地相对湿度与风场配合，峰顶晴空并不自动等于谷底有云海。")
    elif viewing == "peak_overlook":
        hints.append("观云模式为峰顶俯瞰：关注远方谷地是否被云填满，而非脚下薄雾。")

    if cs_vals and max(cs_vals) < 35:
        hints.append("逐时云海概率均 <35%，与上述气象信号整体偏「抑制云海」一致，宜在解读中说明而非仅复述数字。")

    return hints


def _ml_factor_summary(hours: list[dict], date_key: str) -> list[str]:
    lines: list[str] = []
    for h in _hour_rows_for_date(hours, date_key):
        if not h.get("is_sunrise_window"):
            continue
        factors = (h.get("cloudsea") or {}).get("factors") or {}
        for key in ("ml_model", "ml_factor_1", "ml_factor_2", "fuzzy_reference"):
            fd = factors.get(key)
            if fd and isinstance(fd, dict) and fd.get("label"):
                val = fd.get("value") or fd.get("description") or ""
                lines.append(f"{fd.get('label')}: {val}")
        break
    return lines[:6]


def _model_calibration_note(spot_id: str | None, viewpoint_id: str | None) -> dict[str, Any]:
    if not spot_id or not viewpoint_id:
        return {"enabled": False}
    status = get_ml_status(spot_id, viewpoint_id)
    note: dict[str, Any] = {
        "ml_active": status.get("ml_active"),
        "mode": status.get("mode"),
        "eligible_labels": status.get("eligible_labels"),
        "total_labels": status.get("total_labels"),
        "message": status.get("message"),
    }
    if status.get("has_spot_model"):
        try:
            import pickle
            from pathlib import Path

            from app.engine.ml_eligibility import spot_model_path

            path = spot_model_path(spot_id, viewpoint_id)
            if path.is_file():
                with open(path, "rb") as f:
                    art = pickle.load(f)
                note["model_version"] = art.get("version")
                note["loocv_accuracy"] = art.get("loocv_accuracy")
                note["loocv_f1"] = art.get("loocv_f1")
                note["n_training_days"] = art.get("n_days")
                note["decision_threshold"] = art.get("decision_threshold")
                note["tuning_c"] = art.get("tuning_c")
        except Exception as exc:
            note["artifact_error"] = str(exc)
    return note


def build_advisory_context(prediction: dict[str, Any], date_key: str) -> dict[str, Any]:
    loc = prediction.get("location") or {}
    hours = prediction.get("hours") or []
    days = prediction.get("days") or []
    day_summary = next((d for d in days if d.get("date") == date_key), None)

    spot_id = loc.get("spot_id")
    viewpoint_id = loc.get("viewpoint_id")

    window_rows = _sunrise_window_table(hours, date_key)
    ui_scores = _ui_display_scores(hours, day_summary)
    ml_status = loc.get("ml_status") or get_ml_status(spot_id, viewpoint_id)
    precip_prev = _precip_prev_day(hours, date_key)
    ctx = {
        "date": date_key,
        "probability_note": (
            "凌晨出行解读必须以 ui_display_scores 与 hourly_forecast_sunrise_window 的云海概率为准；"
            "day_summary.peak_cloudsea_prob 是全天最大值（常出现在午后），与主页卡片不一致，禁止用于是否推荐前往。"
        ),
        "ui_display_scores": ui_scores,
        "prediction_pipeline": _build_prediction_pipeline(
            hours, day_summary, ml_status, ui_scores, date_key
        ),
        "meteo_analysis_hints": _build_meteo_analysis_hints(window_rows, precip_prev, loc),
        "location": {
            "name": loc.get("name"),
            "lat": loc.get("lat"),
            "lng": loc.get("lng"),
            "elevation_m": loc.get("elevation"),
            "viewing_mode": loc.get("viewing_mode"),
            "viewing_mode_note": loc.get("viewing_mode_note"),
        },
        "day_summary": day_summary,
        "precip_previous_day_mm": precip_prev,
        "hourly_forecast_sunrise_window": window_rows,
        "sunrise_window_stats": _sunrise_window_stats(window_rows),
        "ml_status": ml_status,
        "ml_calibration": _model_calibration_note(spot_id, viewpoint_id),
        "ml_factor_lines": _ml_factor_summary(hours, date_key),
        "observable": loc.get("observable"),
        "terrain": loc.get("terrain"),
    }
    ctx["verdict_hint"] = _compute_verdict_hint(day_summary, window_rows, ui_scores)
    # 兼容旧字段名
    ctx["sunrise_window_hours"] = window_rows
    return ctx


def _advisory_fingerprint(context: dict[str, Any]) -> str:
    """相同日期 + 气象/ML 输入 → 同一缓存键，预报更新后自动失效。"""
    payload = {
        "day_summary": context.get("day_summary"),
        "precip_previous_day_mm": context.get("precip_previous_day_mm"),
        "hourly_forecast_sunrise_window": context.get("hourly_forecast_sunrise_window"),
        "verdict_hint": context.get("verdict_hint"),
        "prediction_pipeline": context.get("prediction_pipeline"),
        "meteo_analysis_hints": context.get("meteo_analysis_hints"),
        "ml_factor_lines": context.get("ml_factor_lines"),
        "ml_status": {
            k: (context.get("ml_status") or {}).get(k)
            for k in ("ml_active", "mode", "eligible_labels")
        },
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _cache_key(
    spot_id: str | None,
    viewpoint_id: str | None,
    date_key: str,
    lat: float,
    lng: float,
    fingerprint: str,
) -> str:
    sid = spot_id or f"{lat:.4f}_{lng:.4f}"
    vid = viewpoint_id or "_"
    return f"llm_advisory:v5:{sid}:{vid}:{date_key}:{fingerprint}"


async def generate_daily_brief(
    prediction: dict[str, Any],
    date_key: str,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    loc = prediction.get("location") or {}
    lat = float(loc.get("lat") or 0)
    lng = float(loc.get("lng") or 0)
    spot_id = loc.get("spot_id")
    viewpoint_id = loc.get("viewpoint_id")

    if not settings.llm_advisory_enabled:
        return {
            "enabled": False,
            "date": date_key,
            "message": "AI 出行解读未启用（服务端未配置 LLM_ADVISORY_ENABLED）",
            "context": build_advisory_context(prediction, date_key),
        }

    if not settings.llm_api_key.strip():
        return {
            "enabled": False,
            "date": date_key,
            "message": "AI 出行解读未配置 API Key（LLM_API_KEY）",
            "context": build_advisory_context(prediction, date_key),
        }

    context = build_advisory_context(prediction, date_key)
    fingerprint = _advisory_fingerprint(context)
    cache_key = _cache_key(spot_id, viewpoint_id, date_key, lat, lng, fingerprint)

    if not force_refresh:
        cached = cache_get(cache_key)
        if cached and isinstance(cached, dict):
            cached["cached"] = True
            return cached
    user_payload = json.dumps(context, ensure_ascii=False, indent=2)
    hint = context.get("verdict_hint") or {}
    user_prompt = (
        f"请为以下日期生成出行解读。目标日期：{date_key}\n"
        f"系统建议判断倾向（必须全文一致采用）：{hint.get('verdict')} — {hint.get('rationale')}\n\n"
        f"结构化数据（JSON）：\n{user_payload}"
    )

    url = settings.llm_base_url.rstrip("/") + "/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 1400,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.llm_advisory_timeout_sec) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except Exception as exc:
        log.warning("llm advisory failed: %s", exc)
        return {
            "enabled": True,
            "date": date_key,
            "error": str(exc),
            "message": "大模型请求失败，请稍后重试",
            "context": context,
        }

    result = {
        "enabled": True,
        "date": date_key,
        "model": settings.llm_model,
        "brief": content.strip(),
        "context": context,
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "cached": False,
    }
    cache_set(cache_key, result, ttl=settings.llm_advisory_cache_ttl)
    return result
