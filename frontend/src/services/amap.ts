import AMapLoader from '@amap/amap-jsapi-loader'
import { AMAP_KEY, AMAP_SECURITY } from '../config'

declare global {
  interface Window {
    AMap: any
    _AMapSecurityConfig?: { securityJsCode: string }
  }
}

let loading: Promise<void> | null = null
let markerRef: any = null
let infoWindowRef: any = null
let mapClickCleanup: (() => void) | null = null
let mapContainerSeq = 0
let mapInitGeneration = 0

export function nextMapInitGeneration(): number {
  return ++mapInitGeneration
}

export function isMapInitAlive(generation: number): boolean {
  return generation === mapInitGeneration
}

export function cancelMapInits(): void {
  mapInitGeneration += 1
}

export function assignMapContainerId(container: HTMLElement): string {
  const id = `yunhai-amap-${++mapContainerSeq}-${Date.now().toString(36)}`
  container.id = id
  return id
}

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

export function loadAmap(): Promise<void> {
  if (!AMAP_KEY || !AMAP_SECURITY) {
    return Promise.reject(new Error('未配置高德地图 Key / 安全密钥'))
  }
  if (window.AMap) return Promise.resolve()
  if (loading) return loading

  window._AMapSecurityConfig = { securityJsCode: AMAP_SECURITY }

  loading = AMapLoader.load({
    key: AMAP_KEY,
    version: '2.0',
  })
    .then(() => {
      if (!window.AMap) throw new Error('高德 SDK 未就绪')
    })
    .catch((err) => {
      loading = null
      throw err
    })

  return loading
}

export function scheduleMapResize(map: any) {
  const run = () => map?.resize?.()
  run()
  window.requestAnimationFrame(run)
  window.setTimeout(run, 120)
  window.setTimeout(run, 400)
}

export function createMap(
  container: HTMLElement,
  lng: number,
  lat: number,
  zoom = 12,
): Promise<any> {
  if (!container.isConnected) {
    return Promise.reject(new Error('地图容器未挂载到 DOM'))
  }
  if (container.clientWidth < 8 || container.clientHeight < 8) {
    return Promise.reject(new Error('地图容器尺寸为零'))
  }

  const id = assignMapContainerId(container)
  const el = document.getElementById(id)
  if (!el || el !== container) {
    return Promise.reject(new Error('地图容器 id 无效'))
  }

  return new Promise((resolve, reject) => {
    let settled = false
    const timer = window.setTimeout(() => {
      if (settled) return
      settled = true
      reject(new Error('地图瓦片加载超时，请检查高德控制台域名白名单是否包含 yunhai.timkj.com'))
    }, 20000)

    try {
      const map = new window.AMap.Map(id, {
        center: [lng, lat],
        zoom,
        resizeEnable: true,
      })

      const onComplete = () => {
        if (settled) return
        settled = true
        window.clearTimeout(timer)
        scheduleMapResize(map)
        resolve(map)
      }

      map.on('complete', onComplete)

      if (typeof map.getStatus === 'function' && map.getStatus() === 'complete') {
        onComplete()
      }
    } catch (err) {
      window.clearTimeout(timer)
      reject(err)
    }
  })
}

function readLngLat(point: any): { lng: number; lat: number } | null {
  if (!point) return null
  const lng = typeof point.getLng === 'function' ? point.getLng() : point.lng
  const lat = typeof point.getLat === 'function' ? point.getLat() : point.lat
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
  if (markerRef) map.remove(markerRef)
  infoWindowRef?.close?.()

  const marker = new window.AMap.Marker({
    position: [lng, lat],
    draggable: Boolean(options?.draggable),
  })
  map.add(marker)
  markerRef = marker

  if (options?.draggable && options.onMove) {
    marker.on('dragend', () => {
      const pos = readLngLat(marker.getPosition?.())
      if (pos) options.onMove!(pos.lng, pos.lat)
    })
  }

  const hint = options?.draggable
    ? '<div style="font-size:10px;color:#64748b;margin-top:2px;">可拖动调整位置</div>'
    : ''
  infoWindowRef = new window.AMap.InfoWindow({
    content: `<div style="font-size:12px;padding:2px 4px;">${label}${hint}</div>`,
    offset: new window.AMap.Pixel(0, -28),
  })
  infoWindowRef.open(map, [lng, lat])
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
  map.on('click', handler)
  mapClickCleanup = () => map.off('click', handler)
}

export function getMapViewportBounds(map: any): CloudBounds | null {
  const bounds = map?.getBounds?.()
  if (!bounds) return null

  const sw = bounds.getSouthWest?.()
  const ne = bounds.getNorthEast?.()
  if (!sw || !ne) return null

  const west = sw.getLng?.() ?? sw.lng
  const south = sw.getLat?.() ?? sw.lat
  const east = ne.getLng?.() ?? ne.lng
  const north = ne.getLat?.() ?? ne.lat
  if ([west, south, east, north].some((v) => typeof v !== 'number' || Number.isNaN(v))) {
    return null
  }

  return { west, south, east, north }
}

function bindMapSync(map: any, handler: () => void): () => void {
  const events = ['mapmove', 'moveend', 'zoomend', 'resize']
  for (const name of events) {
    map.on(name, handler)
  }
  return () => {
    for (const name of events) {
      map.off(name, handler)
    }
  }
}

function computeOverlayRect(map: any, bounds: CloudBounds): OverlayRect | null {
  try {
    const sw = map.lngLatToContainer([bounds.west, bounds.south])
    const ne = map.lngLatToContainer([bounds.east, bounds.north])
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
    map.on(name, debounced)
  }
  return () => {
    if (timer) clearTimeout(timer)
    for (const name of events) {
      map.off(name, debounced)
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
