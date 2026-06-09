import axios from 'axios'
import { FORECAST_HOURS } from '../config'

const api = axios.create({ baseURL: '/api', timeout: 60000 })

export interface SpotSearchResult {
  id: string
  name: string
  region: string
  source: 'curated' | 'tianditu'
  lat?: number
  lng?: number
  peak_elevation?: number
  viewpoint_count?: number
  address?: string
}

export interface Viewpoint {
  id: string
  name: string
  lat: number
  lng: number
  elevation: number
  tags: string[]
  note: string
}

export interface CloudImageResponse {
  bounds: { west: number; south: number; east: number; north: number }
  image_base64: string
  datetime_utc: string
  source: string
  fallback: boolean
  reason?: string | null
  span_lng: number
  span_lat: number
  lookback_hours?: number
  analysis?: {
    cloud_fraction: number
    ir_mean: number
    ir_std: number
    structured: boolean
    uniformity: number
  }
}

export interface CloudRegion {
  span_lng: number
  span_lat: number
}

export interface ScenicSpot {
  id: string
  name: string
  region: string
  peak_elevation: number
  viewpoints: Viewpoint[]
  seasonality: Record<string, unknown>
  cloud_region?: CloudRegion
}

export interface FactorDetail {
  score: number
  weight: number
  label: string
  description: string
  value: string
  reference?: string
}

export interface PredictionScore {
  probability: number
  grade: string
  factors: Record<string, FactorDetail>
  cloud_base_m?: number
  sun_time?: string
}

export interface WeatherSnapshot {
  temperature: number
  humidity: number
  precipitation: number
  cloud_cover: number
  cloud_cover_low: number
  cloud_cover_mid: number
  cloud_cover_high: number
  wind_speed: number
  wind_direction?: number
  wind_gusts?: number
  visibility?: number
  weather_text: string
}

export interface ScenarioPrediction {
  code: string
  label: string
  narrative: string
  level: number
  combined_score: number
}

export interface HourPrediction {
  time: string
  cloudsea: PredictionScore
  sunrise: PredictionScore
  weather: WeatherSnapshot
  scenario: ScenarioPrediction
  is_sunrise_window: boolean
}

export interface DaySummary {
  date: string
  weekday: string
  sunrise_time?: string
  sunrise_hour_index?: number
  /** 日出窗口云海峰值小时（主页卡片默认） */
  sunrise_window_peak_hour_index?: number
  peak_cloudsea_prob: number
  peak_cloudsea_time?: string
  full_day_peak_cloudsea_prob?: number
  full_day_peak_cloudsea_time?: string
  sunrise_window_peak_cloudsea_prob?: number
  sunrise_window_peak_cloudsea_time?: string
  sunrise_scenario_label?: string
  sunrise_combined_score: number
  recommend_periods: string[]
}

export interface MlStatus {
  ml_active: boolean
  mode: 'rule_only' | 'wunvshan_model' | 'spot_model'
  min_labels: number
  eligible_labels: number
  total_labels: number
  rain_excluded_labels: number
  has_spot_model: boolean
  message: string
}

export interface TerrainSummary {
  elev_max_1km_m?: number
  elev_max_5km_m?: number
  relief_5km_m?: number
  elev_viewpoint_m?: number
  sunrise_azimuth_deg?: number
  elev_min_sunrise_15km_m?: number
}

export interface ObservableSummary {
  observable_fraction?: number
  observable_depth_m?: number
  visible_range_km?: number
  fillable_points?: number
  eligible_points?: number
  sunrise_azimuth_deg?: number
  note?: string
  viewer_above_cloud?: boolean
}

export interface PredictResponse {
  location: {
    lat: number
    lng: number
    elevation: number
    name: string
    spot_id?: string
    viewpoint_id?: string
    ml_status?: MlStatus
    viewing_mode?: string
    viewing_mode_note?: string
    terrain?: TerrainSummary
    observable?: ObservableSummary
  }
  hours: HourPrediction[]
  days: DaySummary[]
  best_windows: {
    cloudsea: Array<{ start: string; end: string; peak_prob: number }>
    sunrise: Array<{
      date: string
      prob: number
      combined?: number
      sun_time?: string
      grade: string
      scenario?: string
    }>
  }
  forecast_meta?: Record<string, unknown>
}

export async function searchSpots(
  q: string,
  options?: { lat?: number; lng?: number; curatedOnly?: boolean },
) {
  const { data } = await api.get<{ results: SpotSearchResult[] }>('/spots/search', {
    params: {
      q,
      lat: options?.lat,
      lng: options?.lng,
      curated_only: options?.curatedOnly ?? true,
    },
  })
  return data.results
}

export async function getSpot(id: string) {
  const { data } = await api.get<ScenicSpot>(`/spots/${id}`)
  return data
}

export async function predictViewpoint(spotId: string, viewpointId: string) {
  const { data } = await api.get<PredictResponse>(`/predict/${spotId}/viewpoint/${viewpointId}`)
  return data
}

export async function predictCustom(payload: {
  lat: number
  lng: number
  elevation?: number
  name?: string
  spot_id?: string
}) {
  const { data } = await api.post<PredictResponse>('/predict', { ...payload, hours: FORECAST_HOURS })
  return data
}

export interface DailyAdvisoryResponse {
  enabled: boolean
  date: string
  brief?: string
  message?: string
  error?: string
  model?: string
  cached?: boolean
  generated_at?: string
  context?: Record<string, unknown>
}

export async function fetchDailyAdvisory(body: {
  date: string
  prediction: PredictResponse
  refresh?: boolean
}) {
  const { data } = await api.post<DailyAdvisoryResponse>('/advisory/daily-brief', body, {
    timeout: 90000,
  })
  return data
}

export interface MeteoProfileLevel {
  pressure_hpa: number
  height_m_asl: number
  cloud_cover_pct?: number | null
  rh_pct?: number | null
}

export interface MeteoProfileHour {
  time: string
  levels: MeteoProfileLevel[]
  viewpoint_elevation_m?: number | null
  cloud_base_estimate_m?: number | null
}

export interface MeteoProfileResponse {
  date: string
  source: string
  model_note: string
  lat: number
  lng: number
  elevation?: number | null
  hours: MeteoProfileHour[]
  levels_hpa: number[]
}

export async function fetchMeteoProfile(params: {
  lat: number
  lng: number
  date: string
  elevation?: number
}) {
  const { data } = await api.get<MeteoProfileResponse>('/meteo/profile', { params })
  return data
}

export interface ShareSnapshotResponse {
  id: string
  url: string
  expires_at: string
}

export async function createShareSnapshot(body: {
  date: string
  prediction: PredictResponse
  include_ai?: boolean
  ai_brief?: string
  privacy?: 'hide_coords' | 'show_coords'
}) {
  const { data } = await api.post<ShareSnapshotResponse>('/share/snapshot', body, {
    timeout: 60000,
  })
  return data
}

export async function fetchCloudImage(params: {
  lat: number
  lng: number
  time: string
  spot_id?: string
  west?: number
  south?: number
  east?: number
  north?: number
}) {
  const { data } = await api.get<CloudImageResponse>('/satellite/cloud', { params })
  return data
}

export default api
