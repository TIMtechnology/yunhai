from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.services.share_og_renderer import render_share_image, render_share_og
from app.services.share_store import create_share_snapshot, get_share_snapshot

router = APIRouter(tags=["share"])


class ShareSnapshotRequest(BaseModel):
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    prediction: dict
    include_ai: bool = False
    ai_brief: str | None = None
    privacy: str = "hide_coords"


@router.post("/api/share/snapshot")
async def create_snapshot(body: ShareSnapshotRequest, request: Request):
    if not body.prediction.get("hours"):
        raise HTTPException(status_code=400, detail="prediction 缺少 hours")
    ip = request.client.host if request.client else "unknown"
    try:
        snap = create_share_snapshot(
            prediction=body.prediction,
            date_key=body.date,
            include_ai=body.include_ai,
            ai_brief=body.ai_brief,
            privacy=body.privacy,
            requester_ip=ip,
        )
    except ValueError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return {"id": snap["id"], "url": snap["url"], "expires_at": snap["expires_at"]}


@router.get("/api/share/{share_id}")
async def read_snapshot(share_id: str):
    snap = get_share_snapshot(share_id)
    if not snap:
        raise HTTPException(status_code=410, detail="分享已过期或不存在")
    return snap


@router.get("/api/share/{share_id}/og.png")
async def share_og(share_id: str):
    snap = get_share_snapshot(share_id)
    if not snap:
        raise HTTPException(status_code=410, detail="分享已过期或不存在")
    return Response(content=render_share_og(snap), media_type="image/png")


@router.get("/api/share/{share_id}/image.png")
async def share_image(share_id: str):
    """Standalone share image for users who only want to save/copy the generated PNG."""
    snap = get_share_snapshot(share_id)
    if not snap:
        raise HTTPException(status_code=410, detail="分享已过期或不存在")
    return Response(
        content=render_share_image(snap),
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{share_id}.png"'},
    )


def _escape(s: object) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


@router.get("/s/{share_id}", response_class=HTMLResponse)
async def share_page(share_id: str):
    snap = get_share_snapshot(share_id)
    if not snap:
        return HTMLResponse("<!doctype html><meta charset='utf-8'><title>分享已过期</title><body>分享已过期或不存在。</body>", status_code=410)
    loc = snap.get("location") or {}
    target = snap.get("target") or {}
    scores = snap.get("scores") or {}
    title = f"{loc.get('display_name') or '云海日出'} {target.get('date') or ''} {scores.get('verdict') or ''}".strip()
    desc = f"云海 {scores.get('cloudsea_prob_pct') or '—'}% · 日出 {scores.get('sunrise_prob_pct') or '—'}% · 综合 {scores.get('combined_score') or '—'}"
    snap_json = json.dumps(snap, ensure_ascii=False)
    image_url = f"/api/share/{share_id}/image.png"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{_escape(title)}</title>
  <meta property="og:title" content="{_escape(title)}" />
  <meta property="og:description" content="{_escape(desc)}" />
  <meta property="og:image" content="{_escape(snap.get('og_image_url'))}" />
  <meta name="description" content="{_escape(desc)}" />
  <style>
    body{{margin:0;background:#0f172a;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;}}
    .wrap{{max-width:860px;margin:auto;padding:24px;}}
    .card{{background:#1e293b;border:1px solid #334155;border-radius:18px;padding:20px;margin:12px 0;}}
    .scores{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;}}
    .score{{background:#0f172a;border-radius:14px;padding:14px;}}
    b{{font-size:28px;color:#7dd3fc;display:block;}}
    table{{width:100%;border-collapse:collapse;font-size:13px}}td,th{{border:1px solid #334155;padding:7px}}th{{background:#0f172a}}
    a{{color:#7dd3fc}}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="card">
      <p style="color:#94a3b8;margin:0 0 8px">日出云海预测分享</p>
      <h1 style="margin:0;color:#f8fafc">{_escape(loc.get('display_name'))}</h1>
      <p>{_escape(target.get('date'))} · {_escape(target.get('weekday'))} · 日出 {_escape(target.get('sunrise_time'))}</p>
      <h2 style="color:#fcd34d">{_escape(scores.get('verdict'))}</h2>
      <div class="scores">
        <div class="score"><span>云海</span><b>{_escape(scores.get('cloudsea_prob_pct'))}%</b><small>{_escape(scores.get('cloudsea_grade'))}</small></div>
        <div class="score"><span>日出</span><b style="color:#fb923c">{_escape(scores.get('sunrise_prob_pct'))}%</b><small>{_escape(scores.get('sunrise_grade'))}</small></div>
        <div class="score"><span>综合</span><b style="color:#86efac">{_escape(scores.get('combined_score'))}</b><small>{_escape(scores.get('scenario_label'))}</small></div>
      </div>
    </section>
    <section class="card">
      <h2>依据</h2>
      <table><thead><tr><th>时间</th><th>云海</th><th>日出</th><th>气温</th><th>湿度</th><th>能见度</th></tr></thead><tbody id="rows"></tbody></table>
    </section>
    <section class="card">
      <h2>气象机理</h2>
      <ul id="hints"></ul>
      <p style="color:#94a3b8">AI 辅助解读，数值预测以系统为准，观赏受天气突变影响。</p>
      <p><a href="{_escape(image_url)}" target="_blank">打开分享图（可长按保存/转发）</a> · <a href="/">打开完整预测</a></p>
    </section>
  </main>
  <script>
  const snap = {snap_json};
  const rows = (snap.evidence && snap.evidence.hourly_evidence_table || []).map(r =>
    `<tr><td>${{r.time||''}}</td><td>${{r.cloudsea_pct??'—'}}%</td><td>${{r.sunrise_pct??'—'}}%</td><td>${{r.temp_c??'—'}}℃</td><td>${{r.rh_pct??'—'}}%</td><td>${{r.vis_km??'—'}}km</td></tr>`
  ).join('');
  document.getElementById('rows').innerHTML = rows;
  document.getElementById('hints').innerHTML = (snap.evidence && snap.evidence.meteo_analysis_hints || []).map(h => `<li>${{h}}</li>`).join('');
  </script>
</body>
</html>"""
