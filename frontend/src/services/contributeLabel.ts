import { contributorHeaders } from './contributor'

const API_BASE = import.meta.env.VITE_API_BASE || ''

export type LabelStatus = 'none' | 'partial' | 'full'
export type SunriseQuality = 'visible' | 'blocked' | 'unshootable'
export type ReviewStatus = 'pending' | 'approved' | 'rejected'

export interface CloudseaLabel {
  id?: number
  spot_id?: string
  viewpoint_id?: string
  location_id?: string
  date: string
  status: LabelStatus
  sunrise_quality?: SunriseQuality | null
  notes?: string
  review_status?: ReviewStatus
}

export interface CommunityLocation {
  id: string
  name: string
  lat: number
  lng: number
  elevation?: number
  approved_label_count?: number
  curated_spot_id?: string
}

export interface ContributorStats {
  contributor_id: string
  labels_total: number
  labels_approved: number
  labels_pending: number
  labels_rejected: number
  labels_today: number
  daily_cap: number
  locations_count: number
  locations_cap: number
  quota_date: string
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

export interface RainWindowInfo {
  has_rain: boolean
  rainy_hours: string[]
  max_precip_mm: number
  total_precip_mm: number
  excluded_from_training: boolean
  hint: string
}

export interface LabelSession {
  mode: 'curated' | 'community' | 'coordinates'
  spot_id: string
  viewpoint_id: string
  location_id?: string
  location_name?: string
  lat?: number
  lng?: number
  elevation?: number
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
  stats?: ContributorStats
  data_source?: 'live_forecast' | 'historical_forecast'
  ml_status?: MlStatus
  rain_window?: RainWindowInfo
  viewing_mode?: string
  viewing_mode_note?: string
  observable?: import('./api').ObservableSummary
}

export interface CalendarEntry {
  date: string
  status: string
  review_status?: ReviewStatus
  sunrise_quality?: SunriseQuality | null
}

async function parseError(resp: Response): Promise<string> {
  const text = await resp.text()
  try {
    const data = JSON.parse(text)
    return data.detail || text
  } catch {
    return text || resp.statusText
  }
}

export async function fetchContributorStats(): Promise<ContributorStats> {
  const resp = await fetch(`${API_BASE}/api/contribute/cloudsea/stats`, {
    headers: contributorHeaders(),
  })
  if (!resp.ok) throw new Error(await parseError(resp))
  return resp.json()
}

export async function fetchMyLocations(): Promise<CommunityLocation[]> {
  const resp = await fetch(`${API_BASE}/api/contribute/locations/mine`, {
    headers: contributorHeaders(),
  })
  if (!resp.ok) throw new Error(await parseError(resp))
  const data = await resp.json()
  return data.locations as CommunityLocation[]
}

export async function fetchPublicLocation(locationId: string): Promise<CommunityLocation> {
  const resp = await fetch(`${API_BASE}/api/contribute/locations/${locationId}`)
  if (!resp.ok) throw new Error(await parseError(resp))
  return resp.json()
}

export async function fetchLocationByCuratedSpot(spotId: string): Promise<CommunityLocation | null> {
  const resp = await fetch(`${API_BASE}/api/contribute/locations/by-curated/${encodeURIComponent(spotId)}`)
  if (resp.status === 404) return null
  if (!resp.ok) throw new Error(await parseError(resp))
  return resp.json()
}

export async function updateCommunityLocation(
  locationId: string,
  body: { name?: string; lat?: number; lng?: number; elevation?: number | null },
): Promise<CommunityLocation> {
  const resp = await fetch(`${API_BASE}/api/contribute/locations/${locationId}`, {
    method: 'PATCH',
    headers: contributorHeaders(),
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new Error(await parseError(resp))
  const data = await resp.json()
  return data.location as CommunityLocation
}

export async function fetchContributeLabelSession(params: {
  spotId?: string
  viewpointId?: string
  locationId?: string
  lat?: number
  lng?: number
  name?: string
  elevation?: number
  date: string
}): Promise<LabelSession> {
  const q = new URLSearchParams({ date: params.date })
  if (params.locationId) q.set('location_id', params.locationId)
  else if (params.lat != null && params.lng != null) {
    q.set('lat', String(params.lat))
    q.set('lng', String(params.lng))
    if (params.name) q.set('name', params.name)
    if (params.elevation != null) q.set('elevation', String(params.elevation))
  } else if (params.spotId && params.viewpointId) {
    q.set('spot_id', params.spotId)
    q.set('viewpoint_id', params.viewpointId)
  }
  const resp = await fetch(`${API_BASE}/api/contribute/cloudsea/label-session?${q}`, {
    headers: contributorHeaders(),
  })
  if (!resp.ok) throw new Error(await parseError(resp))
  return resp.json()
}

export async function saveContributeLabel(body: {
  spot_id?: string
  viewpoint_id?: string
  location_id?: string
  lat?: number
  lng?: number
  name?: string
  elevation?: number
  date: string
  status: LabelStatus
  sunrise_quality?: SunriseQuality | null
  notes?: string
}) {
  const resp = await fetch(`${API_BASE}/api/contribute/cloudsea/labels`, {
    method: 'POST',
    headers: contributorHeaders(),
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new Error(await parseError(resp))
  return resp.json() as Promise<{ label: CloudseaLabel; message: string; stats: ContributorStats }>
}

export async function fetchContributeCalendar(params: {
  spotId?: string
  viewpointId?: string
  locationId?: string
  month: string
}): Promise<CalendarEntry[]> {
  const q = new URLSearchParams({ month: params.month })
  if (params.locationId) q.set('location_id', params.locationId)
  else if (params.spotId && params.viewpointId) {
    q.set('spot_id', params.spotId)
    q.set('viewpoint_id', params.viewpointId)
  }
  const resp = await fetch(`${API_BASE}/api/contribute/cloudsea/calendar?${q}`, {
    headers: contributorHeaders(),
  })
  if (!resp.ok) throw new Error(await parseError(resp))
  const data = await resp.json()
  return data.labels as CalendarEntry[]
}

export function buildLabelPageUrl(params: {
  loc?: string
  lat?: number
  lng?: number
  name?: string
  elevation?: number
  spot?: string
  vp?: string
  date?: string
}): string {
  const q = new URLSearchParams()
  if (params.loc) q.set('loc', params.loc)
  if (params.lat != null) q.set('lat', String(params.lat))
  if (params.lng != null) q.set('lng', String(params.lng))
  if (params.name) q.set('name', params.name)
  if (params.elevation != null) q.set('elevation', String(params.elevation))
  if (params.spot) q.set('spot', params.spot)
  if (params.vp) q.set('vp', params.vp)
  if (params.date) q.set('date', params.date)
  return `/label.html?${q.toString()}`
}

export function buildPredictPageUrl(params: {
  loc?: string
  lat?: number
  lng?: number
  name?: string
  spot?: string
  vp?: string
}): string {
  const q = new URLSearchParams()
  if (params.loc) q.set('loc', params.loc)
  if (params.lat != null) q.set('lat', String(params.lat))
  if (params.lng != null) q.set('lng', String(params.lng))
  if (params.name) q.set('name', params.name)
  if (params.spot) q.set('spot', params.spot)
  if (params.vp) q.set('vp', params.vp)
  const qs = q.toString()
  return qs ? `/?${qs}` : '/'
}

export const SUNRISE_QUALITY_LABEL: Record<SunriseQuality, string> = {
  visible: '日出可见',
  blocked: '日出遮挡',
  unshootable: '不可拍',
}

export function sunriseQualityText(q?: string | null) {
  if (!q) return '—'
  return SUNRISE_QUALITY_LABEL[q as SunriseQuality] || q
}
