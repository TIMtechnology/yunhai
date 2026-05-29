const API_BASE = import.meta.env.VITE_API_BASE || ''

function headers(token: string): HeadersInit {
  return {
    'Content-Type': 'application/json',
    'X-Cloudsea-Token': token,
  }
}

export type LabelStatus = 'none' | 'partial' | 'full'

export interface CloudseaLabel {
  id?: number
  spot_id: string
  viewpoint_id: string
  date: string
  status: LabelStatus
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
    scenario: { label: string }
    weather: Record<string, unknown>
  }>
}

export async function fetchSpots() {
  const resp = await fetch(`${API_BASE}/api/spots/search?q=&curated_only=true`)
  if (!resp.ok) throw new Error('加载景区失败')
  const data = await resp.json()
  return data.results as Array<{ id: string; name: string }>
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
  return resp.json() as Promise<{ labels: Array<{ date: string; status: string }> }>
}

export async function fetchAccuracy(token: string, spotId: string, viewpointId: string) {
  const q = new URLSearchParams({ spot_id: spotId, viewpoint_id: viewpointId })
  const resp = await fetch(`${API_BASE}/api/internal/cloudsea/accuracy?${q}`, {
    headers: headers(token),
  })
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
