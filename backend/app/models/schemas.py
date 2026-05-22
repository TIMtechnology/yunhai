from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field


class CloudRegion(BaseModel):
    span_lng: float = 1.8
    span_lat: float = 1.2


class Viewpoint(BaseModel):
    id: str
    name: str
    lat: float
    lng: float
    elevation: float
    tags: List[str] = Field(default_factory=list)
    note: str = ""


class ScenicSpot(BaseModel):
    id: str
    name: str
    aliases: List[str] = Field(default_factory=list)
    region: str
    peak_elevation: float
    viewpoints: List[Viewpoint]
    seasonality: dict = Field(default_factory=dict)
    rules: dict = Field(default_factory=dict)
    cloud_region: Optional[CloudRegion] = None
    source: str = "curated"


class SpotSearchResult(BaseModel):
    id: str
    name: str
    region: str = ""
    source: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    peak_elevation: Optional[float] = None
    viewpoint_count: int = 0
    address: Optional[str] = None


class FactorDetail(BaseModel):
    score: float
    weight: float
    label: str
    description: str
    value: str = ""


class PredictionScore(BaseModel):
    probability: int
    grade: str
    factors: Dict[str, FactorDetail]
    cloud_base_m: Optional[float] = None
    sun_time: Optional[str] = None


class WeatherSnapshot(BaseModel):
    temperature: float
    humidity: float
    precipitation: float
    cloud_cover: float
    cloud_cover_low: float
    cloud_cover_mid: float
    cloud_cover_high: float
    wind_speed: float
    visibility: Optional[float] = None
    weather_text: str = ""


class ScenarioPrediction(BaseModel):
    code: str
    label: str
    narrative: str
    level: int
    combined_score: int


class HourPrediction(BaseModel):
    time: str
    cloudsea: PredictionScore
    sunrise: PredictionScore
    weather: WeatherSnapshot
    scenario: ScenarioPrediction
    is_sunrise_window: bool = False


class DaySummary(BaseModel):
    date: str
    weekday: str
    sunrise_time: Optional[str] = None
    sunrise_hour_index: Optional[int] = None
    peak_cloudsea_prob: int = 0
    peak_cloudsea_time: Optional[str] = None
    sunrise_scenario_label: Optional[str] = None
    sunrise_combined_score: int = 0
    recommend_periods: List[str] = Field(default_factory=list)


class BestWindow(BaseModel):
    start: str
    end: str
    peak_prob: int


class PredictRequest(BaseModel):
    lat: float
    lng: float
    elevation: Optional[float] = None
    name: str = "自定义位置"
    spot_id: Optional[str] = None
    hours: int = 120


class PredictResponse(BaseModel):
    location: dict
    hours: List[HourPrediction]
    days: List[DaySummary] = Field(default_factory=list)
    best_windows: Dict[str, List[Union[BestWindow, dict]]]
