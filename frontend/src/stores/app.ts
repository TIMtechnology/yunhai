import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { DaySummary, HourPrediction, PredictResponse, ScenicSpot, SpotSearchResult, Viewpoint } from '../services/api'
import { getSpot, predictCustom, predictViewpoint, searchSpots } from '../services/api'
import { searchTiandituPoi } from '../services/tiandituPoi'

export interface CloudBounds {
  west: number
  south: number
  east: number
  north: number
}

export interface CloudDebugState {
  mode: 'off' | 'loading' | 'satellite' | 'fallback'
  imageUrl: string | null
  bounds: CloudBounds | null
  datetimeUtc: string
  lookbackHours: number
  imageBytes: number
  reason: string | null
  overlayRect: { left: number; top: number; width: number; height: number } | null
  error: string | null
}

export interface MarkerPosition {
  lat: number
  lng: number
}

const emptyCloudDebug = (): CloudDebugState => ({
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

export const useAppStore = defineStore('app', () => {
  const searchQuery = ref('')
  const searchResults = ref<SpotSearchResult[]>([])
  const poiSuggestions = ref<SpotSearchResult[]>([])
  const currentSpot = ref<ScenicSpot | null>(null)
  const selectedViewpoint = ref<Viewpoint | null>(null)
  const prediction = ref<PredictResponse | null>(null)
  const selectedHourIndex = ref(0)
  const selectedDayIndex = ref(0)
  const showCloudOverlay = ref(false)
  const showCloudDebug = ref(false)
  const cloudDebug = ref<CloudDebugState>(emptyCloudDebug())
  const loading = ref(false)
  const error = ref('')
  const poiSearchError = ref('')

  /** 用户手动调整后的标记坐标；null 表示使用默认坐标 */
  const markerOverride = ref<MarkerPosition | null>(null)
  /** 点击地图放置标记模式 */
  const mapClickPlaceMode = ref(false)
  /** 最近一次 POI 选点（用于恢复默认） */
  const lastPoiSelection = ref<SpotSearchResult | null>(null)

  const days = computed<DaySummary[]>(() => prediction.value?.days ?? [])

  const selectedDay = computed(() => days.value[selectedDayIndex.value] ?? null)

  const dayHourIndices = computed(() => {
    if (!prediction.value || !selectedDay.value) return []
    return prediction.value.hours
      .map((h, idx) => ({ h, idx }))
      .filter(({ h }) => h.time.startsWith(selectedDay.value!.date))
      .map(({ idx }) => idx)
  })

  const dayHours = computed(() =>
    dayHourIndices.value.map((idx) => prediction.value!.hours[idx]),
  )

  const dayLocalIndex = computed(() => {
    const pos = dayHourIndices.value.indexOf(selectedHourIndex.value)
    return pos >= 0 ? pos : 0
  })

  const curatedResults = computed(() =>
    searchResults.value.filter((item) => item.source === 'curated'),
  )

  const poiResults = computed(() =>
    searchResults.value.filter((item) => item.source === 'tianditu'),
  )

  const markerAdjusted = computed(() => markerOverride.value !== null)

  const mapCoords = computed(() => {
    if (markerOverride.value) return markerOverride.value
    if (selectedViewpoint.value) {
      return { lat: selectedViewpoint.value.lat, lng: selectedViewpoint.value.lng }
    }
    if (prediction.value) {
      return { lat: prediction.value.location.lat, lng: prediction.value.location.lng }
    }
    return null
  })

  function clearMarkerOverride() {
    markerOverride.value = null
  }

  async function doSearch(q: string, _center?: MarkerPosition) {
    searchQuery.value = q
    poiSearchError.value = ''
    if (!q.trim()) {
      searchResults.value = []
      return
    }

    const curatedPromise = searchSpots(q, { curatedOnly: true })
    const poiPromise = searchTiandituPoi(q, { count: 12 }).catch((err: Error) => {
      poiSearchError.value = err.message || '天地图 POI 搜索失败'
      return [] as SpotSearchResult[]
    })

    const [curated, poi] = await Promise.all([curatedPromise, poiPromise])
    searchResults.value = [...curated, ...poi]
  }

  async function loadPoiSuggestions(keyword: string, center?: MarkerPosition) {
    const q = keyword.trim()
    poiSearchError.value = ''
    if (!q) {
      poiSuggestions.value = []
      return
    }
    try {
      poiSuggestions.value = await searchTiandituPoi(q, {
        lat: center?.lat,
        lng: center?.lng,
        count: 8,
        regionalBias: true,
      })
    } catch (err: any) {
      poiSearchError.value = err?.message || '天地图 POI 搜索失败'
      poiSuggestions.value = []
    }
  }

  function selectDay(index: number) {
    selectedDayIndex.value = index
    const day = days.value[index]
    if (day?.sunrise_hour_index != null) {
      selectedHourIndex.value = day.sunrise_hour_index
    } else if (dayHourIndices.value.length) {
      selectedHourIndex.value = dayHourIndices.value[0]
    }
  }

  function selectHourByDayLocal(localIdx: number) {
    const globalIdx = dayHourIndices.value[localIdx]
    if (globalIdx != null) selectedHourIndex.value = globalIdx
  }

  function jumpToSunrise() {
    const day = selectedDay.value
    if (day?.sunrise_hour_index != null) {
      selectedHourIndex.value = day.sunrise_hour_index
    }
  }

  function jumpToPeakCloudsea() {
    if (!prediction.value || !selectedDay.value) return
    const indices = dayHourIndices.value
    let bestIdx = indices[0]
    let best = 0
    for (const idx of indices) {
      const prob = prediction.value.hours[idx].cloudsea.probability
      if (prob > best) {
        best = prob
        bestIdx = idx
      }
    }
    selectedHourIndex.value = bestIdx
  }

  async function selectSpot(spotId: string) {
    loading.value = true
    error.value = ''
    clearMarkerOverride()
    lastPoiSelection.value = null
    try {
      currentSpot.value = await getSpot(spotId)
      selectedViewpoint.value = currentSpot.value.viewpoints[0] ?? null
      if (selectedViewpoint.value) {
        await loadPrediction(spotId, selectedViewpoint.value.id)
        const vp = selectedViewpoint.value
        await loadPoiSuggestions(`${currentSpot.value.name} ${vp.name}`, {
          lat: vp.lat,
          lng: vp.lng,
        })
      }
    } catch (e: any) {
      error.value = e?.message || '加载景区失败'
    } finally {
      loading.value = false
    }
  }

  async function selectViewpoint(vp: Viewpoint) {
    selectedViewpoint.value = vp
    clearMarkerOverride()
    if (currentSpot.value) {
      await loadPrediction(currentSpot.value.id, vp.id)
      await loadPoiSuggestions(`${currentSpot.value.name} ${vp.name}`, {
        lat: vp.lat,
        lng: vp.lng,
      })
    }
  }

  async function loadPrediction(spotId: string, viewpointId: string) {
    loading.value = true
    error.value = ''
    try {
      prediction.value = await predictViewpoint(spotId, viewpointId)
      selectedDayIndex.value = 0
      const firstDay = prediction.value.days[0]
      selectedHourIndex.value = firstDay?.sunrise_hour_index ?? 0
    } catch (e: any) {
      error.value = e?.message || '预测失败'
    } finally {
      loading.value = false
    }
  }

  async function predictAt(
    lat: number,
    lng: number,
    name = '自定义位置',
    elevation?: number,
    spotId?: string,
    options?: { keepSpot?: boolean; markAdjusted?: boolean },
  ) {
    loading.value = true
    error.value = ''
    if (!options?.keepSpot) {
      currentSpot.value = null
      selectedViewpoint.value = null
      poiSuggestions.value = []
    }
    if (options?.markAdjusted) {
      markerOverride.value = { lat, lng }
    }
    try {
      prediction.value = await predictCustom({ lat, lng, name, elevation, spot_id: spotId })
      selectedDayIndex.value = 0
      selectedHourIndex.value = prediction.value.days[0]?.sunrise_hour_index ?? 0
    } catch (e: any) {
      error.value = e?.message || '预测失败'
    } finally {
      loading.value = false
    }
  }

  async function moveMarkerTo(lat: number, lng: number) {
    markerOverride.value = { lat, lng }

    if (currentSpot.value && selectedViewpoint.value) {
      const spot = currentSpot.value
      const vp = selectedViewpoint.value
      await predictAt(
        lat,
        lng,
        `${spot.name} · ${vp.name}（已调整）`,
        vp.elevation,
        spot.id,
        { keepSpot: true, markAdjusted: true },
      )
      return
    }

    if (prediction.value) {
      const loc = prediction.value.location
      await predictAt(
        lat,
        lng,
        `${loc.name.replace(/（已调整）$/, '')}（已调整）`,
        loc.elevation,
        loc.spot_id,
        { markAdjusted: true },
      )
    }
  }

  async function resetMarkerPosition() {
    clearMarkerOverride()
    if (currentSpot.value && selectedViewpoint.value) {
      await loadPrediction(currentSpot.value.id, selectedViewpoint.value.id)
      return
    }
    if (lastPoiSelection.value?.lat != null && lastPoiSelection.value.lng != null) {
      await predictAt(
        lastPoiSelection.value.lat,
        lastPoiSelection.value.lng,
        lastPoiSelection.value.name,
      )
    }
  }

  async function applyPoiLocation(item: SpotSearchResult) {
    if (item.lat == null || item.lng == null) return
    lastPoiSelection.value = item
    clearMarkerOverride()
    if (currentSpot.value && selectedViewpoint.value) {
      await predictAt(
        item.lat,
        item.lng,
        `${currentSpot.value.name} · ${selectedViewpoint.value.name}（${item.name}）`,
        selectedViewpoint.value.elevation,
        currentSpot.value.id,
      )
      return
    }
    await predictAt(item.lat, item.lng, item.name)
  }

  async function selectPoiResult(item: SpotSearchResult) {
    clearMarkerOverride()
    lastPoiSelection.value = item
    if (item.source === 'curated') {
      await selectSpot(item.id)
      return
    }
    if (item.lat != null && item.lng != null) {
      currentSpot.value = null
      selectedViewpoint.value = null
      poiSuggestions.value = []
      await predictAt(item.lat, item.lng, item.name)
    }
  }

  const currentHour = (): HourPrediction | null => {
    if (!prediction.value?.hours.length) return null
    const idx = Math.min(selectedHourIndex.value, prediction.value.hours.length - 1)
    return prediction.value.hours[idx]
  }

  function setCloudDebug(partial: Partial<CloudDebugState>) {
    cloudDebug.value = { ...cloudDebug.value, ...partial }
  }

  function resetCloudDebug() {
    cloudDebug.value = emptyCloudDebug()
  }

  return {
    searchQuery,
    searchResults,
    poiSuggestions,
    curatedResults,
    poiResults,
    currentSpot,
    selectedViewpoint,
    prediction,
    selectedHourIndex,
    selectedDayIndex,
    showCloudOverlay,
    showCloudDebug,
    cloudDebug,
    markerOverride,
    mapClickPlaceMode,
    markerAdjusted,
    mapCoords,
    days,
    selectedDay,
    dayHourIndices,
    dayHours,
    dayLocalIndex,
    loading,
    error,
    poiSearchError,
    doSearch,
    loadPoiSuggestions,
    selectSpot,
    selectViewpoint,
    predictAt,
    moveMarkerTo,
    resetMarkerPosition,
    applyPoiLocation,
    selectPoiResult,
    selectDay,
    selectHourByDayLocal,
    jumpToSunrise,
    jumpToPeakCloudsea,
    currentHour,
    setCloudDebug,
    resetCloudDebug,
  }
})
