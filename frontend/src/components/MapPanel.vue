<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fetchCloudImage, type WeatherSnapshot } from '../services/api'
import {
  addMarker,
  attachCloudImageOverlay,
  bindMapClickForMarker,
  bindMapViewportChange,
  cleanupMapInteractions,
  createMap,
  getMapViewportBounds,
  loadTianditu,
  removeCloudImageOverlay,
} from '../services/tianditu'
import { useAppStore } from '../stores/app'

const props = defineProps<{
  lng: number
  lat: number
  label: string
  zoom?: number
  weather?: WeatherSnapshot | null
  weatherTime?: string
  spotId?: string
  cloudBase?: number | null
  elevation?: number
  showOverlay?: boolean
  class?: string
}>()

const store = useAppStore()
const wrapRef = ref<HTMLElement | null>(null)
const container = ref<HTMLElement | null>(null)
const canvasRef = ref<HTMLCanvasElement | null>(null)
let map: any = null
let resizeObserver: ResizeObserver | null = null
let fetchToken = 0
let cloudCleanup: (() => void) | null = null
let viewportCleanup: (() => void) | null = null
let cloudBlobUrl: string | null = null
let debugBlobUrl: string | null = null
let markerMoveTimer: ReturnType<typeof setTimeout> | null = null

type CloudMode = 'off' | 'loading' | 'satellite' | 'fallback'

const cloudMode = ref<CloudMode>('off')
const cloudMeta = ref<{
  datetimeUtc: string
  spanLng: number
  spanLat: number
  lookbackHours: number
} | null>(null)

const overlayLegend = computed(() => {
  if (!props.weather) return ''
  const w = props.weather
  return `低 ${w.cloud_cover_low}% · 中 ${w.cloud_cover_mid}% · 高 ${w.cloud_cover_high}% · 总 ${w.cloud_cover}%`
})

const overlayModeText = computed(() => {
  if (!props.showOverlay) return '已关闭'
  if (cloudMode.value === 'loading') return '加载卫星云图…'
    if (cloudMode.value === 'satellite') {
    const utc = cloudMeta.value?.datetimeUtc
    const lookback = cloudMeta.value?.lookbackHours ?? 0
    const timeText = utc ? `UTC ${utc}` : '最新观测'
    if (lookback > 0) return `Himawari 红外 · ${timeText}（回溯 ${lookback}h）`
    return `Himawari 红外 · ${timeText}`
  }
  if (cloudMode.value === 'fallback') return '暂无有效卫星图 · 气象示意蒙版'
  return '等待气象数据…'
})

const badgeText = computed(() => {
  if (cloudMode.value === 'satellite') return '卫星'
  if (cloudMode.value === 'fallback') return '示意'
  if (cloudMode.value === 'loading') return '加载'
  return ''
})

const badgeClass = computed(() => {
  if (cloudMode.value === 'satellite') return 'bg-sky-500/15 text-sky-300'
  if (cloudMode.value === 'fallback') return 'bg-amber-500/15 text-amber-300'
  return 'bg-slate-500/15 text-slate-400'
})

const showCanvasFallback = computed(
  () => props.showOverlay && props.weather && cloudMode.value === 'fallback',
)

function revokeCloudBlob() {
  if (cloudBlobUrl) {
    URL.revokeObjectURL(cloudBlobUrl)
    cloudBlobUrl = null
  }
}

function revokeDebugBlob() {
  if (debugBlobUrl) {
    URL.revokeObjectURL(debugBlobUrl)
    debugBlobUrl = null
  }
}

function clearCloudOverlay() {
  removeCloudImageOverlay(cloudCleanup)
  cloudCleanup = null
  revokeCloudBlob()
}

function base64ToBlobUrl(base64: string): string {
  revokeCloudBlob()
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i)
  }
  cloudBlobUrl = URL.createObjectURL(new Blob([bytes], { type: 'image/jpeg' }))
  return cloudBlobUrl
}

function setDebugPreview(base64: string) {
  revokeDebugBlob()
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i)
  }
  debugBlobUrl = URL.createObjectURL(new Blob([bytes], { type: 'image/jpeg' }))
  return debugBlobUrl
}

function updateDebug(partial: Parameters<typeof store.setCloudDebug>[0]) {
  store.setCloudDebug(partial)
}

function drawCloudCanvas() {
  const canvas = canvasRef.value
  const wrap = wrapRef.value
  if (!canvas || !wrap || !showCanvasFallback.value) {
    if (canvas) {
      const ctx = canvas.getContext('2d')
      ctx?.clearRect(0, 0, canvas.width, canvas.height)
    }
    return
  }

  const w = props.weather!
  const width = wrap.clientWidth
  const height = wrap.clientHeight
  if (width <= 0 || height <= 0) return

  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  ctx.clearRect(0, 0, width, height)

  const low = w.cloud_cover_low / 100
  const mid = w.cloud_cover_mid / 100
  const high = w.cloud_cover_high / 100
  const total = w.cloud_cover / 100
  const density = Math.min(1, low * 0.45 + mid * 0.3 + high * 0.25)

  if (density < 0.08 && w.precipitation <= 0.05) return

  const fogGrad = ctx.createLinearGradient(0, height * 0.35, 0, height)
  fogGrad.addColorStop(0, 'rgba(56, 189, 248, 0)')
  fogGrad.addColorStop(0.45, `rgba(56, 189, 248, ${0.12 + low * 0.35})`)
  fogGrad.addColorStop(1, `rgba(148, 163, 184, ${0.2 + low * 0.45})`)
  ctx.fillStyle = fogGrad
  ctx.fillRect(0, height * 0.25, width, height * 0.75)

  const blobCount = Math.round(4 + density * 10)
  for (let i = 0; i < blobCount; i += 1) {
    const seed = i * 9973 + Math.round(total * 1000)
    const bx = ((seed * 73) % 1000) / 1000
    const by = ((seed * 131) % 1000) / 1000
    const r = width * (0.08 + ((seed * 17) % 100) / 500)
    const cx = bx * width
    const cy = by * height * 0.75 + height * 0.1
    const layer = i % 3 === 0 ? low : i % 3 === 1 ? mid : high
    const alpha = 0.08 + layer * 0.35
    const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, r)
    grad.addColorStop(0, `rgba(226, 232, 240, ${alpha})`)
    grad.addColorStop(0.55, `rgba(148, 163, 184, ${alpha * 0.65})`)
    grad.addColorStop(1, 'rgba(148, 163, 184, 0)')
    ctx.fillStyle = grad
    ctx.beginPath()
    ctx.ellipse(cx, cy, r, r * 0.55, 0, 0, Math.PI * 2)
    ctx.fill()
  }

  if (w.precipitation > 0.1) {
    ctx.fillStyle = `rgba(71, 85, 105, ${Math.min(0.45, w.precipitation * 0.15)})`
    ctx.fillRect(0, 0, width, height)
  }

  if (high > 0.2) {
    ctx.fillStyle = `rgba(203, 213, 225, ${high * 0.22})`
    ctx.fillRect(0, 0, width, height * 0.45)
  }
}

interface CloudImageBounds {
  west: number
  south: number
  east: number
  north: number
}

function applySatelliteOverlay(imageBase64: string, bounds: CloudImageBounds) {
  const host = wrapRef.value
  if (!map || !host) throw new Error('地图容器未就绪')

  clearCloudOverlay()
  const url = base64ToBlobUrl(imageBase64)
  cloudCleanup = attachCloudImageOverlay(map, host, url, bounds, 0.85, (rect) => {
    updateDebug({ overlayRect: rect })
  })
}

async function refreshCloudLayer() {
  const token = ++fetchToken

  if (!map || !props.showOverlay || !props.weatherTime) {
    cloudMode.value = 'off'
    cloudMeta.value = null
    clearCloudOverlay()
    updateDebug({
      mode: 'off',
      imageUrl: null,
      bounds: null,
      datetimeUtc: '',
      lookbackHours: 0,
      imageBytes: 0,
      reason: null,
      overlayRect: null,
      error: null,
    })
    drawCloudCanvas()
    return
  }

  const viewport = getMapViewportBounds(map)
  if (!viewport) {
    updateDebug({ mode: 'loading', error: '无法读取地图视口范围' })
    return
  }

  cloudMode.value = 'loading'
  updateDebug({ mode: 'loading', error: null, imageUrl: null })

  try {
    const data = await fetchCloudImage({
      lat: props.lat,
      lng: props.lng,
      time: props.weatherTime,
      spot_id: props.spotId,
      west: viewport.west,
      south: viewport.south,
      east: viewport.east,
      north: viewport.north,
    })
    if (token !== fetchToken) return

    if (!data.fallback && data.image_base64) {
      const previewUrl = setDebugPreview(data.image_base64)
      const bytes = atob(data.image_base64).length
      applySatelliteOverlay(data.image_base64, data.bounds)
      cloudMode.value = 'satellite'
      cloudMeta.value = {
        datetimeUtc: data.datetime_utc,
        spanLng: data.span_lng,
        spanLat: data.span_lat,
        lookbackHours: data.lookback_hours ?? 0,
      }
      updateDebug({
        mode: 'satellite',
        imageUrl: previewUrl,
        bounds: data.bounds,
        datetimeUtc: data.datetime_utc,
        lookbackHours: data.lookback_hours ?? 0,
        imageBytes: bytes,
        reason: data.analysis
          ? `区域云量≈${data.analysis.cloud_fraction}% · IR μ${data.analysis.ir_mean}`
          : data.reason ?? (data.lookback_hours ? `回溯 ${data.lookback_hours}h` : null),
        error: null,
      })
      drawCloudCanvas()
      return
    }

    clearCloudOverlay()
    cloudMode.value = 'fallback'
    cloudMeta.value = {
      datetimeUtc: '',
      spanLng: data.span_lng,
      spanLat: data.span_lat,
      lookbackHours: 0,
    }
    updateDebug({
      mode: 'fallback',
      imageUrl: null,
      bounds: data.bounds,
      datetimeUtc: '',
      lookbackHours: 0,
      imageBytes: 0,
      reason: data.reason ?? '无有效卫星图',
      overlayRect: null,
      error: null,
    })
    drawCloudCanvas()
  } catch (err) {
    console.warn('[MapPanel] 卫星云图叠加失败，回退示意蒙版', err)
    if (token !== fetchToken) return
    clearCloudOverlay()
    cloudMode.value = 'fallback'
    cloudMeta.value = null
    updateDebug({
      mode: 'fallback',
      imageUrl: null,
      bounds: null,
      error: err instanceof Error ? err.message : '叠加失败',
      overlayRect: null,
    })
    drawCloudCanvas()
  }
}

function setupViewportSync() {
  viewportCleanup?.()
  if (!map) return
  viewportCleanup = bindMapViewportChange(map, () => {
    if (props.showOverlay && props.weatherTime) {
      refreshCloudLayer()
    }
  })
}

function scheduleMarkerMove(lng: number, lat: number) {
  if (markerMoveTimer) clearTimeout(markerMoveTimer)
  markerMoveTimer = setTimeout(() => {
    store.moveMarkerTo(lat, lng)
  }, 400)
}

function syncMarker() {
  if (!map) return
  addMarker(map, props.lng, props.lat, props.label, {
    draggable: !!store.prediction,
    onMove: (lng, lat) => scheduleMarkerMove(lng, lat),
  })
  bindMapClickForMarker(map, store.mapClickPlaceMode, (lng, lat) => {
    addMarker(map, lng, lat, props.label, {
      draggable: !!store.prediction,
      onMove: (mLng, mLat) => scheduleMarkerMove(mLng, mLat),
    })
    scheduleMarkerMove(lng, lat)
  })
}

async function initMap() {
  if (!container.value) return
  await loadTianditu()
  map = createMap(container.value, props.lng, props.lat, props.zoom ?? 13)
  syncMarker()
  setupViewportSync()
  await refreshCloudLayer()
}

watch(
  () => [props.lng, props.lat, props.label],
  () => {
    syncMarker()
  },
)

watch(
  () => store.mapClickPlaceMode,
  () => {
    syncMarker()
  },
)

watch(
  () => [props.weatherTime, props.showOverlay, props.lat, props.lng, props.spotId],
  () => {
    refreshCloudLayer()
  },
)

watch(showCanvasFallback, () => drawCloudCanvas())

onMounted(() => {
  initMap()
  if (wrapRef.value) {
    resizeObserver = new ResizeObserver(() => {
      drawCloudCanvas()
      if (cloudMode.value === 'satellite') refreshCloudLayer()
    })
    resizeObserver.observe(wrapRef.value)
  }
})

onBeforeUnmount(() => {
  if (markerMoveTimer) clearTimeout(markerMoveTimer)
  resizeObserver?.disconnect()
  viewportCleanup?.()
  cleanupMapInteractions()
  clearCloudOverlay()
  revokeDebugBlob()
  store.resetCloudDebug()
  map = null
})
</script>

<template>
  <div
    ref="wrapRef"
    class="relative h-full min-h-0 w-full overflow-hidden rounded-2xl border border-slate-800"
    :class="props.class"
  >
    <div ref="container" class="absolute inset-0 z-0" />
    <canvas
      ref="canvasRef"
      class="pointer-events-none absolute inset-0 z-[500] transition-opacity duration-700"
      :class="showCanvasFallback ? 'opacity-100' : 'opacity-0'"
    />
    <div
      v-if="store.prediction"
      class="absolute right-3 top-3 z-[600] w-[220px] rounded-xl border border-slate-600/80 bg-slate-950/90 px-3 py-2 text-[11px] backdrop-blur"
    >
      <div class="mb-1.5 font-medium text-sky-300">定位控制</div>
      <div class="text-slate-400">拖动标记或开启点击模式，微调观景点 GPS</div>
      <div v-if="store.markerAdjusted && store.mapCoords" class="mt-1 text-amber-300">
        已手动调整 · {{ store.mapCoords.lat.toFixed(5) }}, {{ store.mapCoords.lng.toFixed(5) }}
      </div>
      <div class="mt-2 flex flex-wrap gap-1.5">
        <n-button
          size="tiny"
          :type="store.mapClickPlaceMode ? 'primary' : 'default'"
          @click="store.mapClickPlaceMode = !store.mapClickPlaceMode"
        >
          {{ store.mapClickPlaceMode ? '点击放置中' : '点击放置' }}
        </n-button>
        <n-button
          v-if="store.markerAdjusted"
          size="tiny"
          tertiary
          @click="store.resetMarkerPosition()"
        >
          恢复默认
        </n-button>
      </div>
    </div>
    <div
      v-if="showOverlay"
      class="absolute left-3 top-3 z-[600] max-w-[260px] rounded-xl border border-slate-600/80 bg-slate-950/85 px-3 py-2 text-[11px] backdrop-blur"
    >
      <div class="mb-1 flex items-center justify-between gap-2">
        <span class="font-medium text-sky-300">云层蒙版</span>
        <span v-if="badgeText" class="rounded px-1.5 py-0.5 text-[9px]" :class="badgeClass">
          {{ badgeText }}
        </span>
      </div>
      <div class="text-slate-400">{{ overlayModeText }}</div>
      <div v-if="weather" class="mt-1 text-slate-300">{{ overlayLegend }}</div>
      <div v-if="cloudMeta && cloudMode === 'satellite'" class="mt-1 text-slate-500">
        覆盖当前地图视口 · 裁切约 {{ (cloudMeta.spanLng * 2).toFixed(2) }}°×{{
          (cloudMeta.spanLat * 2).toFixed(2)
        }}°
      </div>
      <div v-if="cloudMode === 'satellite'" class="mt-1 text-sky-500/80">
        缩放/平移地图会自动刷新云图裁切范围
      </div>
      <div v-if="cloudBase != null && elevation != null" class="mt-1 text-slate-500">
        云底≈{{ Math.round(cloudBase) }}m · 观景点 {{ Math.round(elevation) }}m
      </div>
      <div v-if="!weather" class="mt-1 text-slate-500">等待气象数据…</div>
    </div>
    <slot />
  </div>
</template>
