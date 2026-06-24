const API_BASE = import.meta.env.VITE_API_BASE || ''

function headers(token: string): HeadersInit {
  return {
    'Content-Type': 'application/json',
    'X-Cloudsea-Token': token,
  }
}

export type LabelStatus = 'none' | 'partial' | 'full'
export type SunriseQuality = 'visible' | 'blocked' | 'unshootable'

export interface CloudseaLabel {
  id?: number
  spot_id: string
  viewpoint_id: string
  date: string
  status: LabelStatus
  sunrise_quality?: SunriseQuality | null
  notes?: string
  confidence?: string
}

export interface LabelSession {
  spot_id: string
  viewpoint_id: string
  date: string
  label: CloudseaLabel | null
  raw_meteo: Array<Record<string, unknown>>
  sunrise_window_summary: {
    max_cloudsea_prob: number
    scenario: string
    peak_time?: string
  } | null
  hours: Array<{
    time: string
    cloudsea: { probability: number }
    scenario: { label: string; combined_score?: number }
    weather: Record<string, unknown>
  }>
}

export async function fetchSpots() {
  const resp = await fetch(`${API_BASE}/api/spots/search?q=&curated_only=true`)
  if (!resp.ok) throw new Error('加载景区失败')
  const data = await resp.json()
  return data.results as Array<{ id: string; name: string; lat?: number; lng?: number; region?: string }>
}

export async function fetchSpotDetail(spotId: string) {
  const resp = await fetch(`${API_BASE}/api/spots/${spotId}`)
  if (!resp.ok) throw new Error('加载景区详情失败')
  return resp.json()
}

export async function fetchLabelSession(
  token: string,
  spotId: string,
  viewpointId: string,
  date: string,
): Promise<LabelSession> {
  const q = new URLSearchParams({ spot_id: spotId, viewpoint_id: viewpointId, date })
  const resp = await fetch(`${API_BASE}/api/internal/cloudsea/label-session?${q}`, {
    headers: headers(token),
  })
  if (!resp.ok) throw new Error(await resp.text())
  return resp.json()
}

export async function saveLabel(token: string, body: CloudseaLabel) {
  const resp = await fetch(`${API_BASE}/api/internal/cloudsea/labels`, {
    method: 'POST',
    headers: headers(token),
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new Error(await resp.text())
  return resp.json()
}

export async function fetchCalendar(token: string, spotId: string, viewpointId: string, month: string) {
  const q = new URLSearchParams({ spot_id: spotId, viewpoint_id: viewpointId, month })
  const resp = await fetch(`${API_BASE}/api/internal/cloudsea/calendar?${q}`, {
    headers: headers(token),
  })
  if (!resp.ok) throw new Error(await resp.text())
  return resp.json() as Promise<{ labels: Array<{ date: string; status: string; sunrise_quality?: SunriseQuality | null }> }>
}

export async function fetchAccuracy(
  token: string,
  spotId: string,
  viewpointId: string,
  refresh = false,
) {
  const q = new URLSearchParams({ spot_id: spotId, viewpoint_id: viewpointId })
  if (refresh) q.set('refresh', 'true')
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 180_000)
  try {
    const resp = await fetch(`${API_BASE}/api/internal/cloudsea/accuracy?${q}`, {
      headers: headers(token),
      signal: controller.signal,
    })
    if (!resp.ok) throw new Error(await resp.text())
    return resp.json()
  } finally {
    clearTimeout(timer)
  }
}

export interface PredictionHistoryEntry {
  id: number | string
  log_id?: number
  created_at: string
  lead_hours_to_dawn?: number | null
  peak_cloudsea_prob?: number | null
  model_version?: string | null
  page_source?: string | null
  direction_ok?: number | null
  predicted_positive?: number | null
  same_forecast?: boolean
  diagnosis?: { tags?: string[]; summary?: string } | null
}

export interface PredictionScheduledSummary {
  scheduled_count: number
  user_count: number
  forecast_snapshot_count: number
  needs_label_attention: boolean
  banner?: string | null
}

export interface PredictionHistory {
  spot_id: string
  viewpoint_id: string
  target_date: string
  label: CloudseaLabel | null
  access_count: number
  snapshot_count?: number
  snapshot_ids?: number[]
  correct_count: number
  outcome_count: number
  entries: PredictionHistoryEntry[]
  actual_precursor: Array<Record<string, unknown>>
  scheduled_summary?: PredictionScheduledSummary | null
}

export interface PredictionCurvePoint {
  time: string
  label: string
  forecast_rh?: number | null
  actual_rh?: number | null
  forecast_cloud_low?: number | null
  actual_cloud_low?: number | null
  forecast_wind?: number | null
  actual_wind?: number | null
}

export interface PredictionSegmentDiff {
  segment: string
  label: string
  rh_forecast?: number | null
  rh_actual?: number | null
  rh_delta?: number | null
  cloud_low_forecast?: number | null
  cloud_low_actual?: number | null
  cloud_low_delta?: number | null
  wind_forecast?: number | null
  wind_actual?: number | null
}

export interface PredictionSnapshotDetail {
  id: number
  created_at: string
  target_date: string
  lead_hours_to_dawn?: number | null
  peak_cloudsea_prob?: number | null
  model_version?: string | null
  direction_ok?: number | null
  label_status?: string | null
  diagnosis?: { tags?: string[]; summary?: string } | null
  curve_points: PredictionCurvePoint[]
  segments: PredictionSegmentDiff[]
  hourly_errors?: Array<Record<string, unknown>>
  reconciled?: boolean
}

export async function fetchPredictionHistory(
  spotId: string,
  viewpointId: string,
  date: string,
): Promise<PredictionHistory> {
  const q = new URLSearchParams({ spot_id: spotId, viewpoint_id: viewpointId, date })
  const resp = await fetch(`${API_BASE}/api/prediction-feedback/history?${q}`)
  if (!resp.ok) throw new Error(await resp.text())
  return resp.json()
}

export async function fetchPredictionSnapshotDetail(
  accessLogId: number,
): Promise<PredictionSnapshotDetail> {
  const resp = await fetch(`${API_BASE}/api/prediction-feedback/history/${accessLogId}`)
  if (!resp.ok) throw new Error(await resp.text())
  return resp.json()
}

export async function fetchLabels(token: string, spotId: string, viewpointId: string, month: string) {
  const q = new URLSearchParams({ spot_id: spotId, viewpoint_id: viewpointId, month })
  const resp = await fetch(`${API_BASE}/api/internal/cloudsea/labels?${q}`, {
    headers: headers(token),
  })
  if (!resp.ok) throw new Error(await resp.text())
  return resp.json() as Promise<{ labels: CloudseaLabel[] }>
}

export interface ReviewQueueItem {
  id: number
  date: string
  status: LabelStatus
  review_status: string
  spot_id: string
  viewpoint_id: string
  location_id?: string | null
  location_name?: string | null
  community_name?: string | null
  contributor_id?: string | null
  notes?: string | null
  lat?: number | null
  lng?: number | null
}

export async function fetchReviewQueue(token: string, limit = 100) {
  const resp = await fetch(`${API_BASE}/api/internal/cloudsea/review-queue?limit=${limit}`, {
    headers: headers(token),
  })
  if (!resp.ok) throw new Error(await resp.text())
  const data = await resp.json()
  return data.items as ReviewQueueItem[]
}

export async function reviewLabelApi(
  token: string,
  labelId: number,
  reviewStatus: 'approved' | 'rejected',
) {
  const resp = await fetch(`${API_BASE}/api/internal/cloudsea/labels/${labelId}/review`, {
    method: 'POST',
    headers: headers(token),
    body: JSON.stringify({ review_status: reviewStatus }),
  })
  if (!resp.ok) throw new Error(await resp.text())
  return resp.json()
}

export async function curateLocationApi(token: string, locationId: string) {
  const resp = await fetch(`${API_BASE}/api/internal/cloudsea/locations/${locationId}/curate`, {
    method: 'POST',
    headers: headers(token),
  })
  if (!resp.ok) throw new Error(await resp.text())
  return resp.json() as Promise<{ spot_id: string; file: string; location_id: string }>
}

export async function trainModelApi(token: string) {
  const resp = await fetch(`${API_BASE}/api/internal/cloudsea/train`, {
    method: 'POST',
    headers: headers(token),
  })
  if (!resp.ok) throw new Error(await resp.text())
  return resp.json() as Promise<{
    loocv_accuracy?: number
    deploy_recommended?: boolean
    reason?: string
    output?: string
    stdout?: string
  }>
}

const STATUS_LABEL: Record<LabelStatus, string> = {
  none: '无云海',
  partial: '部分',
  full: '完整',
}

export function labelStatusText(status: string) {
  return STATUS_LABEL[status as LabelStatus] || status
}

export const SUNRISE_QUALITY_LABEL: Record<SunriseQuality, string> = {
  visible: '日出可见',
  blocked: '日出遮挡',
  unshootable: '不可拍',
}
