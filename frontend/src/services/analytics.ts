type AnalyticsPayload = Record<string, unknown>

function collectUrl(): string {
  const base = import.meta.env.VITE_API_BASE || ''
  return `${base}/api/analytics/collect`
}

export function trackEvent(event: string, payload: AnalyticsPayload = {}) {
  const body = JSON.stringify({
    event,
    payload,
    page_url: window.location.href,
    referrer: document.referrer || '',
  })

  if (navigator.sendBeacon) {
    const blob = new Blob([body], { type: 'application/json' })
    if (navigator.sendBeacon(collectUrl(), blob)) return
  }

  fetch(collectUrl(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true,
  }).catch(() => {})
}

export function trackPageVisit() {
  const params = new URLSearchParams(window.location.search)
  trackEvent('page_visit', {
    landing_url: window.location.href,
    utm_source: params.get('utm_source') || '',
    utm_medium: params.get('utm_medium') || '',
    utm_campaign: params.get('utm_campaign') || '',
  })
}

export function trackSearch(keyword: string, curatedCount: number, poiCount: number) {
  trackEvent('search', {
    keyword,
    curated_count: curatedCount,
    poi_count: poiCount,
    result_count: curatedCount + poiCount,
  })
}

export function trackPoiSearch(keyword: string, resultCount: number) {
  trackEvent('poi_search', { keyword, result_count: resultCount })
}

export function trackSpotSelect(spotId: string, spotName: string) {
  trackEvent('spot_select', { spot_id: spotId, spot_name: spotName })
}

export function trackViewpointSelect(spotId: string, viewpointId: string, name: string) {
  trackEvent('viewpoint_select', {
    spot_id: spotId,
    viewpoint_id: viewpointId,
    name,
  })
}

export function trackPredictCustom(lat: number, lng: number, name: string, spotId?: string) {
  trackEvent('predict_custom', { lat, lng, name, spot_id: spotId || '' })
}
