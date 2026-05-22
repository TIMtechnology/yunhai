import { TIANDITU_KEY } from '../config'

declare global {
  interface Window {
    T: any
  }
}

let loading: Promise<void> | null = null
let markerRef: any = null
let infoWindowRef: any = null
let mapClickCleanup: (() => void) | null = null

export interface CloudBounds {
  west: number
  south: number
  east: number
  north: number
}

export interface OverlayRect {
  left: number
  top: number
  width: number
  height: number
}

export interface MarkerOptions {
  draggable?: boolean
  onMove?: (lng: number, lat: number) => void
}

export function loadTianditu(): Promise<void> {
  if (window.T) return Promise.resolve()
  if (loading) return loading

  loading = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = `https://api.tianditu.gov.cn/api?v=4.0&tk=${TIANDITU_KEY}`
    script.async = true
    script.onload = () => resolve()
    script.onerror = () => reject(new Error('天地图加载失败'))
    document.head.appendChild(script)
  })
  return loading
}

export function createMap(container: HTMLElement, lng: number, lat: number, zoom = 12) {
  const T = window.T
  const map = new T.Map(container)
  map.centerAndZoom(new T.LngLat(lng, lat), zoom)
  map.enableScrollWheelZoom()

  const layer = new T.TileLayer(
    `https://t0.tianditu.gov.cn/vec_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=vec&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=${TIANDITU_KEY}`,
    { minZoom: 1, maxZoom: 18 },
  )
  const labelLayer = new T.TileLayer(
    `https://t0.tianditu.gov.cn/cva_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=cva&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=${TIANDITU_KEY}`,
    { minZoom: 1, maxZoom: 18 },
  )
  map.addLayer(layer)
  map.addLayer(labelLayer)
  return map
}

function readLngLat(point: any): { lng: number; lat: number } | null {
  if (!point) return null
  const lng = point.lng ?? point.getLng?.()
  const lat = point.lat ?? point.getLat?.()
  if (typeof lng !== 'number' || typeof lat !== 'number') return null
  return { lng, lat }
}

export function addMarker(
  map: any,
  lng: number,
  lat: number,
  label: string,
  options?: MarkerOptions,
) {
  const T = window.T
  if (markerRef) map.removeOverLay(markerRef)
  if (infoWindowRef) map.closeInfoWindow()

  const point = new T.LngLat(lng, lat)
  const marker = new T.Marker(point)
  map.addOverLay(marker)
  markerRef = marker

  if (options?.draggable) {
    marker.enableDragging?.()
    marker.addEventListener?.('dragend', () => {
      const pos = readLngLat(marker.getLngLat?.())
      if (pos && options.onMove) options.onMove(pos.lng, pos.lat)
    })
  }

  if (T.InfoWindow) {
    const hint = options?.draggable ? '<div style="font-size:10px;color:#64748b;margin-top:2px;">可拖动调整位置</div>' : ''
    const win = new T.InfoWindow(
      `<div style="font-size:12px;padding:2px 4px;">${label}${hint}</div>`,
      { offset: new T.Point(0, -20) },
    )
    map.openInfoWindow(win, point)
    infoWindowRef = win
  }
  map.panTo(point)
}

export function bindMapClickForMarker(
  map: any,
  enabled: boolean,
  onClick: (lng: number, lat: number) => void,
) {
  mapClickCleanup?.()
  mapClickCleanup = null
  if (!enabled) return

  const handler = (event: any) => {
    const pos = readLngLat(event.lnglat ?? event.lngLat)
    if (pos) onClick(pos.lng, pos.lat)
  }
  map.addEventListener?.('click', handler)
  mapClickCleanup = () => map.removeEventListener?.('click', handler)
}

export function getMapViewportBounds(map: any): CloudBounds | null {
  const bounds = map?.getBounds?.()
  if (!bounds) return null

  const sw = bounds.getSouthWest?.() ?? bounds.Lq ?? bounds.southWest
  const ne = bounds.getNorthEast?.() ?? bounds.kq ?? bounds.northEast
  if (!sw || !ne) return null

  const west = sw.lng ?? sw.getLng?.()
  const south = sw.lat ?? sw.getLat?.()
  const east = ne.lng ?? ne.getLng?.()
  const north = ne.lat ?? ne.getLat?.()
  if ([west, south, east, north].some((v) => typeof v !== 'number' || Number.isNaN(v))) {
    return null
  }

  return { west, south, east, north }
}

function bindMapSync(map: any, handler: () => void): () => void {
  const events = ['move', 'moveend', 'zoomend', 'resize']
  for (const name of events) {
    map.addEventListener?.(name, handler)
  }
  return () => {
    for (const name of events) {
      map.removeEventListener?.(name, handler)
    }
  }
}

function computeOverlayRect(map: any, bounds: CloudBounds): OverlayRect | null {
  const T = window.T
  try {
    const sw = map.lngLatToContainerPoint(new T.LngLat(bounds.west, bounds.south))
    const ne = map.lngLatToContainerPoint(new T.LngLat(bounds.east, bounds.north))
    if (!sw || !ne) return null
    return {
      left: Math.min(sw.x, ne.x),
      top: Math.min(sw.y, ne.y),
      width: Math.max(1, Math.abs(ne.x - sw.x)),
      height: Math.max(1, Math.abs(ne.y - sw.y)),
    }
  } catch {
    return null
  }
}

function applyOverlayRect(
  img: HTMLImageElement,
  frame: HTMLDivElement,
  rect: OverlayRect,
) {
  img.style.left = `${rect.left}px`
  img.style.top = `${rect.top}px`
  img.style.width = `${rect.width}px`
  img.style.height = `${rect.height}px`
  frame.style.left = `${rect.left}px`
  frame.style.top = `${rect.top}px`
  frame.style.width = `${rect.width}px`
  frame.style.height = `${rect.height}px`
}

/** 将卫星云图贴到地图宿主层（wrapRef），避免被天地图瓦片层遮挡 */
export function attachCloudImageOverlay(
  map: any,
  hostEl: HTMLElement,
  imageUrl: string,
  bounds: CloudBounds,
  opacity = 0.82,
  onRectChange?: (rect: OverlayRect | null) => void,
): () => void {
  const frame = document.createElement('div')
  frame.className = 'pointer-events-none absolute z-[450] box-border border border-sky-400/50'
  frame.style.boxShadow = 'inset 0 0 0 1px rgba(56,189,248,0.2)'

  const img = document.createElement('img')
  img.src = imageUrl
  img.alt = '卫星云图'
  img.className = 'pointer-events-none absolute z-[451] select-none'
  img.style.opacity = String(opacity)
  img.style.objectFit = 'fill'
  img.style.filter = 'contrast(1.5) brightness(1.25) saturate(1.3)'

  hostEl.appendChild(frame)
  hostEl.appendChild(img)

  const sync = () => {
    const rect = computeOverlayRect(map, bounds)
    if (!rect) {
      img.style.display = 'none'
      frame.style.display = 'none'
      onRectChange?.(null)
      return
    }
    img.style.display = 'block'
    frame.style.display = 'block'
    applyOverlayRect(img, frame, rect)
    onRectChange?.(rect)
  }

  const unbind = bindMapSync(map, sync)
  img.onload = sync
  sync()

  return () => {
    unbind()
    frame.remove()
    img.remove()
  }
}

export function bindMapViewportChange(map: any, handler: () => void): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null
  const debounced = () => {
    if (timer) clearTimeout(timer)
    timer = setTimeout(handler, 350)
  }
  const events = ['zoomend', 'moveend', 'resize']
  for (const name of events) {
    map.addEventListener?.(name, debounced)
  }
  return () => {
    if (timer) clearTimeout(timer)
    for (const name of events) {
      map.removeEventListener?.(name, debounced)
    }
  }
}

export function removeCloudImageOverlay(_cleanup: (() => void) | null) {
  _cleanup?.()
}

export function cleanupMapInteractions() {
  mapClickCleanup?.()
  mapClickCleanup = null
}
