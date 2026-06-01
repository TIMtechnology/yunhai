from __future__ import annotations

from app.engine.observable_field import observable_cloudsea_evidence
from app.engine.utils import clamp


def weather_text(
    *,
    precip: float,
    cloud_low: float,
    cloud_mid: float,
    cloud_high: float,
    rh: float,
) -> str:
    total = (cloud_low + cloud_mid + cloud_high) / 3
    if precip >= 1.0:
        return "中到大雨"
    if precip > 0.1:
        return "有小雨"
    if cloud_low >= 70 or total >= 75:
        return "低云/阴天"
    if cloud_mid + cloud_high >= 60:
        return "多云"
    if total <= 25 and rh < 70:
        return "晴到少云"
    if rh >= 85 and cloud_low < 15:
        return "湿度大·易起雾"
    return "多云间晴"


def build_scenario(
    *,
    cloudsea_prob: int,
    sunrise_prob: int,
    precip: float,
    cloud_low: float,
    cloud_mid: float,
    cloud_high: float,
    is_sunrise_window: bool = False,
    visibility: float | None = None,
    elevation: float = 0.0,
    rh: float = 70.0,
    viewing_mode: str = "valley_fill",
    terrain: dict | None = None,
    observable: dict | None = None,
) -> dict:
    """联合云海+日出+天气给出观赏场景；云海标签需低云或能见度补偿证据。"""
    from app.engine.cloudsea_scorer import cloudsea_visual_evidence

    total_cloud = (cloud_low + cloud_mid + cloud_high) / 3
    effective_low, has_cloudsea_evidence = cloudsea_visual_evidence(
        cloud_low=cloud_low,
        cloud_mid=cloud_mid,
        visibility=visibility,
        elevation=elevation,
        rh=rh,
    )
    if observable and viewing_mode == "peak_overlook":
        has_cloudsea_evidence = observable_cloudsea_evidence(observable)
    elif observable and viewing_mode == "valley_fill":
        obs_frac = float(observable.get("observable_fraction") or 0)
        vis_km = float(visibility or 99999) / 1000.0
        if obs_frac >= 0.35 and vis_km <= 2.0 and rh >= 62:
            has_cloudsea_evidence = True
        elif vis_km <= 2.0 and rh >= 68 and cloudsea_prob >= 38:
            has_cloudsea_evidence = True
    elif viewing_mode == "peak_overlook" and cloudsea_prob >= 50:
        has_cloudsea_evidence = True

    if precip >= 1.0:
        return _scenario(
            "rain",
            "阴雨不宜",
            "有明显降水，不建议前往观日出或云海。",
            3,
            min(cloudsea_prob, sunrise_prob),
        )

    if precip > 0.1:
        return _scenario(
            "light_rain",
            "小雨影响",
            "有小雨，视野与日出均受影响，不建议专程观赏。",
            3,
            min(cloudsea_prob, sunrise_prob) - 10,
        )

    if viewing_mode == "peak_overlook" and cloudsea_prob >= 55 and has_cloudsea_evidence:
        frac = float((observable or {}).get("observable_fraction") or 0)
        vis_km = float((observable or {}).get("visible_range_km") or 0)
        narrative = (
            f"峰顶条件配合日出方向可见范围内约 {frac:.0%} 地形可填云"
            f"（可见约 {vis_km:.0f} km），较可能看到脚下云海。"
        )
        return _scenario(
            "above_cloudsea",
            "站在云海之上 · 俯瞰",
            narrative,
            1 if cloudsea_prob >= 70 else 2,
            cloudsea_prob,
        )

    if (
        viewing_mode == "peak_overlook"
        and cloudsea_prob >= 55
        and cloud_low >= 55
        and rh >= 85
    ):
        if cloud_mid + cloud_high >= 50:
            return _scenario(
                "cloudsea_block_sun",
                "有云海·日出难",
                "峰顶低云/湿度条件好，可能有云海，但中高云或能见度限制日出。",
                2,
                cloudsea_prob - 5,
            )
        return _scenario(
            "cloudsea_only",
            "或有云海",
            "峰顶湿度与低云条件较好，值得关注云海（日出另说）。",
            2,
            cloudsea_prob,
        )

    if is_sunrise_window or (cloudsea_prob >= 55 and sunrise_prob >= 55):
        if (
            cloudsea_prob >= 70
            and sunrise_prob >= 70
            and has_cloudsea_evidence
            and effective_low >= 30
        ):
            return _scenario(
                "sunrise_cloudsea",
                "日出云海",
                "低云与垂直场条件配合，大概率可见日出金光与山间云海。",
                1,
                int((cloudsea_prob * 0.55 + sunrise_prob * 0.45)),
            )
        if (
            cloudsea_prob >= 55
            and sunrise_prob >= 50
            and has_cloudsea_evidence
            and effective_low >= 18
        ):
            return _scenario(
                "sunrise_cloudsea_fair",
                "较可能日出云海",
                "具备一定云海与日出条件，值得早起碰运气。",
                2,
                int((cloudsea_prob + sunrise_prob) / 2),
            )
        if sunrise_prob >= 60 and not has_cloudsea_evidence:
            if (
                viewing_mode == "valley_fill"
                and visibility is not None
                and visibility <= 2000
                and rh >= 65
                and cloudsea_prob >= 35
            ):
                return _scenario(
                    "sunrise_cloudsea_fair",
                    "较可能日出云海",
                    "能见度偏低且湿度较高，可能有谷地雾/云海，值得早起碰运气。",
                    2,
                    int((cloudsea_prob + sunrise_prob) / 2),
                )
            if cloud_low <= 20 and total_cloud <= 40:
                return _scenario(
                    "clear_sunrise",
                    "晴日日出",
                    "低云偏少，较可能看到日出，但形成观赏级云海概率低。",
                    2,
                    sunrise_prob,
                )
            return _scenario(
                "sunrise_only",
                "可看日出",
                "日出条件尚可，但低云不足，难见云海。",
                2,
                sunrise_prob,
            )

    if cloudsea_prob >= 65 and sunrise_prob < 45 and has_cloudsea_evidence:
        if cloud_low >= 60:
            return _scenario(
                "cloudsea_block_sun",
                "有云海·日出难",
                "低云偏厚，可能有云海但日出易被遮挡。",
                2,
                cloudsea_prob - 5,
            )
        return _scenario(
            "cloudsea_only",
            "或有云海",
            "云海条件较好，但日出观测概率一般。",
            2,
            cloudsea_prob,
        )

    if sunrise_prob >= 65 and (cloudsea_prob < 45 or not has_cloudsea_evidence):
        if cloud_low <= 25 and total_cloud <= 35:
            return _scenario(
                "clear_sunrise",
                "晴日日出",
                "天空较晴朗，适合看日出，但形成云海概率偏低。",
                2,
                sunrise_prob,
            )
        return _scenario(
            "sunrise_only",
            "可看日出",
            "日出条件较好，云海不明显。",
            2,
            sunrise_prob,
        )

    if cloud_low >= 55 and cloud_mid + cloud_high >= 50:
        return _scenario(
            "overcast_no_sun",
            "多云无日出",
            "中高云偏多，日出大概率被挡，不建议等日出。",
            3,
            max(cloudsea_prob, sunrise_prob) - 15,
        )

    if total_cloud <= 30 and cloudsea_prob < 40:
        return _scenario(
            "clear_no_cloudsea",
            "晴空少云",
            "天气晴好，但缺少形成云海的水汽与云量。",
            3,
            sunrise_prob,
        )

    if cloudsea_prob < 35 and sunrise_prob < 35:
        return _scenario(
            "not_recommended",
            "不宜观赏",
            "气象条件一般，不建议专程前往。",
            3,
            min(cloudsea_prob, sunrise_prob),
        )

    return _scenario(
        "fair",
        "条件一般",
        "云海与日出概率均中等，可结合时间轴挑选稍好时段。",
        2,
        int((cloudsea_prob + sunrise_prob) / 2),
    )


def _scenario(code: str, label: str, narrative: str, level: int, combined: int) -> dict:
    return {
        "code": code,
        "label": label,
        "narrative": narrative,
        "level": level,
        "combined_score": int(clamp(combined / 100.0) * 100),
    }
