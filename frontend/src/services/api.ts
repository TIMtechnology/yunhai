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
  peak_cloudsea_prob: number
  peak_cloudsea_time?: string
  sunrise_scenario_label?: string
  sunrise_combined_score: number
  recommend_periods: string[]
}

export interface PredictResponse {
  location: {
    lat: number
    lng: number
    elevation: number
    name: string
    spot_id?: string
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
