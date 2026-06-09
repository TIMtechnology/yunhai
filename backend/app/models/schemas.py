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
    viewing_mode: Optional[str] = None


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
    community_location_id: Optional[str] = None


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
    reference: str = ""


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
    wind_direction: Optional[float] = None
    wind_gusts: Optional[float] = None
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
    """天文日出时刻最近的小时索引（用于「跳到日出」）。"""
    sunrise_window_peak_hour_index: Optional[int] = None
    """日出窗口（03–07）云海概率最高的小时索引（主页卡片默认展示）。"""
    peak_cloudsea_prob: int = 0
    peak_cloudsea_time: Optional[str] = None
    full_day_peak_cloudsea_prob: int = 0
    full_day_peak_cloudsea_time: Optional[str] = None
    sunrise_window_peak_cloudsea_prob: int = 0
    sunrise_window_peak_cloudsea_time: Optional[str] = None
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
    viewpoint_id: Optional[str] = None
    hours: int = 120


class PredictResponse(BaseModel):
    location: dict
    hours: List[HourPrediction]
    days: List[DaySummary] = Field(default_factory=list)
    best_windows: Dict[str, List[Union[BestWindow, dict]]]
    forecast_meta: dict = Field(default_factory=dict)


class TerrainContextResponse(BaseModel):
    lat: float
    lng: float
    source: str
    dem_version: str
    elev_viewpoint_m: float
    elev_open_meteo_m: float
    elev_curated_m: Optional[float] = None
    elev_curated_delta_m: Optional[float] = None
    elev_max_1km_m: float
    elev_min_1km_m: float
    elev_max_5km_m: float
    elev_min_5km_m: float
    relief_1km_m: float
    relief_5km_m: float
    slope_deg: float
    aspect_deg: float
    viewing_mode: str
    viewing_mode_note: str
    viewing_mode_source: str
    sample_counts: dict
    profile_date: Optional[str] = None
    sunrise_azimuth_deg: Optional[float] = None
    elev_profile_sunrise: Optional[List[dict]] = None
    elev_min_sunrise_15km_m: Optional[float] = None
    elev_max_sunrise_30km_m: Optional[float] = None
    sunrise_sector_relief_m: Optional[float] = None
    cloud_layer: Optional[dict] = None
    problems_dem_solves: List[dict] = Field(default_factory=list)
