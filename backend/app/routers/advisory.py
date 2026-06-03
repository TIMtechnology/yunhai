from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.llm_advisory import generate_daily_brief

router = APIRouter(tags=["advisory"])


class DailyAdvisoryRequest(BaseModel):
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    prediction: dict
    refresh: bool = False


@router.post("/api/advisory/daily-brief")
async def daily_brief(body: DailyAdvisoryRequest):
    """根据当前预测结果（含逐时气象 + ML）生成 AI 出行解读。"""
    if not body.prediction.get("hours"):
        raise HTTPException(status_code=400, detail="prediction 缺少 hours")
    return await generate_daily_brief(
        body.prediction,
        body.date,
        force_refresh=body.refresh,
    )
