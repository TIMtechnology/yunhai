<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import type { CalendarEntry, CommunityLocation, ContributorStats, LabelSession, LabelStatus } from '../services/contributeLabel'
import {
  fetchContributeCalendar,
  fetchContributeLabelSession,
  fetchLocationByCuratedSpot,
  fetchMyLocations,
  saveContributeLabel,
} from '../services/contributeLabel'
import { contributorIdShort } from '../services/contributor'
import type { LabelStatus as AdminLabelStatus } from '../services/cloudseaLabel'
import {
  fetchAccuracy,
  fetchCalendar,
  fetchLabelSession,
  fetchSpotDetail,
  fetchSpots,
  saveLabel,
} from '../services/cloudseaLabel'
import type { ReviewQueueItem } from '../services/cloudseaLabel'
import AdminReviewPanel from '../components/AdminReviewPanel.vue'

const TOKEN_KEY = 'cloudsea_admin_token'

const message = useMessage()
const adminMode = ref(new URLSearchParams(location.search).get('admin') === '1')
const token = ref(localStorage.getItem(TOKEN_KEY) || '')

const locationMode = ref<'curated' | 'community'>('curated')
const spots = ref<Array<{ id: string; name: string }>>([])
const viewpoints = ref<Array<{ id: string; name: string }>>([])
const myLocations = ref<CommunityLocation[]>([])
const spotId = ref('wunvshan')
const viewpointId = ref('dianjiangtai')
const locationId = ref('')
const poiLat = ref<number | null>(null)
const poiLng = ref<number | null>(null)
const poiName = ref('')
const poiElevation = ref<number | undefined>(undefined)

const currentDate = ref(new Date().toISOString().slice(0, 10))
const notes = ref('')
const loading = ref(false)
const session = ref<LabelSession | null>(null)
const calendar = ref<Record<string, CalendarEntry>>({})
const stats = ref<ContributorStats | null>(null)
const accuracy = ref<{ total: number; correct: number; accuracy: number | null; details: Array<Record<string, unknown>> } | null>(null)
const selectedStatus = ref<LabelStatus | null>(null)
const linkedCommunityId = ref('')
const pageReady = ref(false)
let loadSessionSeq = 0

interface LabelKeys {
  spotId: string
  viewpointId: string
  locationId: string
}

const month = computed(() => currentDate.value.slice(0, 7))
const communityOptions = computed(() =>
  myLocations.value.map((l) => ({ label: `${l.name} (${l.id})`, value: l.id })),
)
const spotOptions = computed(() => spots.value.map((s) => ({ label: s.name, value: s.id })))
const locationTitle = computed(() => {
  if (locationMode.value === 'community' && locationId.value) {
    return myLocations.value.find((l) => l.id === locationId.value)?.name || poiName.value || locationId.value
  }
  if (poiLat.value != null && poiLng.value != null) return poiName.value || 'POI 点位'
  const spot = spots.value.find((s) => s.id === spotId.value)
  const vp = viewpoints.value.find((v) => v.id === viewpointId.value)
  return `${spot?.name || ''} · ${vp?.name || ''}`.trim()
})

const viewpointOptions = computed(() => viewpoints.value.map((v) => ({ label: v.name, value: v.id })))

const windowScoreSummary = computed(() => {
  const hours = session.value?.hours ?? []
  if (!hours.length) return null
  const peakCloud = hours.reduce(
    (best, h) => (h.cloudsea.probability > best.cloudsea.probability ? h : best),
    hours[0],
  )
  const peakCombined = hours.reduce(
    (best, h) => ((h.scenario.combined_score ?? 0) > (best.scenario.combined_score ?? 0) ? h : best),
    hours[0],
  )
  return { peakCloud, peakCombined }
})

const dataSourceLabel = computed(() => {
  const src = session.value?.data_source
  if (src === 'live_forecast') return '与主页相同 · 实时预报'
  if (src === 'historical_forecast') return '历史预报存档（回测）'
  return ''
})

function parseUrlParams() {
  const q = new URLSearchParams(location.search)
  if (q.get('loc')) {
    locationMode.value = 'community'
    locationId.value = q.get('loc') || ''
  }
  if (q.get('lat') && q.get('lng')) {
    locationMode.value = 'community'
    poiLat.value = Number(q.get('lat'))
    poiLng.value = Number(q.get('lng'))
    poiName.value = q.get('name') || 'POI 点位'
    const elev = q.get('elevation')
    if (elev) poiElevation.value = Number(elev)
  }
  if (q.get('spot')) {
    locationMode.value = 'curated'
    spotId.value = q.get('spot') || spotId.value
  }
  if (q.get('vp')) viewpointId.value = q.get('vp') || viewpointId.value
  if (q.get('date')) currentDate.value = q.get('date') || currentDate.value
}

function clearCommunityCoords() {
  poiLat.value = null
  poiLng.value = null
  poiName.value = ''
  poiElevation.value = undefined
}

function switchToCurated() {
  locationMode.value = 'curated'
  locationId.value = ''
  clearCommunityCoords()
}

function switchToCommunity(preferredId?: string) {
  locationMode.value = 'community'
  clearCommunityCoords()
  if (preferredId) {
    locationId.value = preferredId
    return
  }
  if (!locationId.value && myLocations.value.length) {
    locationId.value = myLocations.value[0].id
  }
}

function onLocationModeChange(mode: 'curated' | 'community') {
  if (mode === 'curated') switchToCurated()
  else switchToCommunity(locationId.value || undefined)
  syncUrl()
}

function syncUrl() {
  const q = new URLSearchParams()
  if (adminMode.value) q.set('admin', '1')
  q.set('date', currentDate.value)
  if (locationMode.value === 'community') {
    if (locationId.value) q.set('loc', locationId.value)
    else if (poiLat.value != null && poiLng.value != null) {
      q.set('lat', String(poiLat.value))
      q.set('lng', String(poiLng.value))
      if (poiName.value) q.set('name', poiName.value)
      if (poiElevation.value != null) q.set('elevation', String(poiElevation.value))
    }
  } else {
    q.set('spot', spotId.value)
    q.set('vp', viewpointId.value)
  }
  history.replaceState(null, '', `${location.pathname}?${q.toString()}`)
}

function shiftDate(days: number) {
  const d = new Date(`${currentDate.value}T12:00:00`)
  d.setDate(d.getDate() + days)
  currentDate.value = d.toISOString().slice(0, 10)
}

async function loadSpots() {
  spots.value = await fetchSpots()
}

async function loadViewpoints() {
  const detail = await fetchSpotDetail(spotId.value)
  viewpoints.value = detail.viewpoints || []
  if (!viewpoints.value.find((v) => v.id === viewpointId.value) && viewpoints.value.length) {
    viewpointId.value = viewpoints.value[0].id
  }
}

async function loadMyLocations() {
  try {
    const locs = await fetchMyLocations()
    myLocations.value = locs
    if (locationId.value && !locs.find((l) => l.id === locationId.value)) {
      // URL 带入的点位可能属于其他贡献者，仍可标注
      myLocations.value = [{ id: locationId.value, name: poiName.value || locationId.value, lat: poiLat.value || 0, lng: poiLng.value || 0 }, ...locs]
    }
  } catch {
    myLocations.value = []
  }
}

function sessionParams() {
  if (locationMode.value === 'community') {
    if (locationId.value) return { locationId: locationId.value, date: currentDate.value }
    if (poiLat.value != null && poiLng.value != null) {
      return {
        lat: poiLat.value,
        lng: poiLng.value,
        name: poiName.value,
        elevation: poiElevation.value,
        date: currentDate.value,
      }
    }
  }
  return { spotId: spotId.value, viewpointId: viewpointId.value, date: currentDate.value }
}

async function resolveLabelKeys(): Promise<LabelKeys> {
  if (locationMode.value === 'community' && locationId.value) {
    linkedCommunityId.value = locationId.value
    return {
      spotId: 'community',
      viewpointId: locationId.value,
      locationId: locationId.value,
    }
  }
  if (locationMode.value === 'curated') {
    const linked = await fetchLocationByCuratedSpot(spotId.value)
    if (linked) {
      linkedCommunityId.value = linked.id
      if (!myLocations.value.find((l) => l.id === linked.id)) {
        myLocations.value = [linked, ...myLocations.value]
      }
      return {
        spotId: 'community',
        viewpointId: linked.id,
        locationId: linked.id,
      }
    }
  }
  linkedCommunityId.value = ''
  return {
    spotId: spotId.value,
    viewpointId: viewpointId.value,
    locationId: '',
  }
}

async function loadSession() {
  const seq = ++loadSessionSeq
  loading.value = true
  try {
    if (adminMode.value && token.value) {
      const keys = await resolveLabelKeys()
      const adminSession = await fetchLabelSession(
        token.value,
        keys.spotId,
        keys.viewpointId,
        currentDate.value,
      )
      session.value = {
        mode: keys.spotId === 'community' ? 'community' : 'curated',
        spot_id: keys.spotId,
        viewpoint_id: keys.viewpointId,
        location_id: keys.locationId || undefined,
        date: currentDate.value,
        label: adminSession.label,
        raw_meteo: adminSession.raw_meteo,
        sunrise_window_summary: adminSession.sunrise_window_summary,
        hours: adminSession.hours,
      }
      selectedStatus.value = (adminSession.label?.status as LabelStatus) || null
      notes.value = adminSession.label?.notes || ''
      const cal = await fetchCalendar(token.value, keys.spotId, keys.viewpointId, month.value)
      calendar.value = Object.fromEntries(
        cal.labels.map((x) => [x.date, { date: x.date, status: x.status, review_status: 'approved' as const }]),
      )
      if (keys.spotId !== 'community') {
        accuracy.value = await fetchAccuracy(token.value, keys.spotId, keys.viewpointId)
      } else {
        accuracy.value = null
      }
      return
    }

    const keys = await resolveLabelKeys()
    if (keys.locationId) {
      const data = await fetchContributeLabelSession({
        locationId: keys.locationId,
        date: currentDate.value,
      })
      session.value = data
      selectedStatus.value = (data.label?.status as LabelStatus) || null
      notes.value = data.label?.notes || ''
      stats.value = data.stats || null
      if (data.location_id) locationId.value = data.location_id
      const entries = await fetchContributeCalendar({
        month: month.value,
        locationId: keys.locationId,
      })
      calendar.value = Object.fromEntries(entries.map((x) => [x.date, x]))
      return
    }

    const params = sessionParams()
    const data = await fetchContributeLabelSession(params)
    session.value = data
    selectedStatus.value = (data.label?.status as LabelStatus) || null
    notes.value = data.label?.notes || ''
    stats.value = data.stats || null
    if (data.location_id) locationId.value = data.location_id

    const entries = await fetchContributeCalendar({
      month: month.value,
      locationId: data.location_id || locationId.value || undefined,
      spotId: data.spot_id,
      viewpointId: data.viewpoint_id,
    })
    calendar.value = Object.fromEntries(entries.map((x) => [x.date, x]))
  } catch (err) {
    if (seq === loadSessionSeq) message.error(String(err))
  } finally {
    if (seq === loadSessionSeq) loading.value = false
  }
}

async function applyLabel(status: LabelStatus) {
  selectedStatus.value = status
  try {
    const keys = await resolveLabelKeys()
    if (adminMode.value && token.value) {
      await saveLabel(token.value, {
        spot_id: keys.spotId,
        viewpoint_id: keys.viewpointId,
        date: currentDate.value,
        status: status as AdminLabelStatus,
        notes: notes.value,
      })
      message.success(`已保存 ${currentDate.value}`)
    } else {
      const body: Parameters<typeof saveContributeLabel>[0] = {
        date: currentDate.value,
        status,
        notes: notes.value,
      }
      if (keys.locationId) {
        body.location_id = keys.locationId
      } else if (poiLat.value != null && poiLng.value != null) {
        body.lat = poiLat.value
        body.lng = poiLng.value!
        body.name = poiName.value
        body.elevation = poiElevation.value
      } else {
        body.spot_id = spotId.value
        body.viewpoint_id = viewpointId.value
      }
      const resp = await saveContributeLabel(body)
      stats.value = resp.stats
      message.success(resp.message)
    }
    await loadSession()
  } catch (err) {
    message.error(String(err))
  }
}

function saveToken() {
  localStorage.setItem(TOKEN_KEY, token.value)
  message.success('Token 已保存')
  loadSession()
}

function openReviewItem(item: ReviewQueueItem) {
  if (item.location_id) {
    switchToCommunity(item.location_id)
    poiName.value = item.location_name || item.community_name || ''
    if (!myLocations.value.find((l) => l.id === item.location_id)) {
      myLocations.value = [
        { id: item.location_id, name: poiName.value || item.location_id, lat: 0, lng: 0 } as CommunityLocation,
        ...myLocations.value,
      ]
    }
  } else {
    switchToCurated()
    spotId.value = item.spot_id
    viewpointId.value = item.viewpoint_id
  }
  currentDate.value = item.date
  syncUrl()
  loadSession()
}

function dayClass(date: string) {
  const entry = calendar.value[date]
  if (!entry) return 'bg-slate-900 border-slate-700'
  const pending = entry.review_status === 'pending'
  const base =
    entry.status === 'full'
      ? 'bg-emerald-600/30 border-emerald-500'
      : entry.status === 'partial'
        ? 'bg-amber-600/30 border-amber-500'
        : 'bg-slate-600/40 border-slate-500'
  return pending ? `${base} ring-1 ring-sky-500/50` : base
}

function buildMonthDays() {
  const [y, m] = month.value.split('-').map(Number)
  const last = new Date(y, m, 0)
  const days: string[] = []
  for (let d = 1; d <= last.getDate(); d++) {
    days.push(`${month.value}-${String(d).padStart(2, '0')}`)
  }
  return days
}

watch([spotId], () => {
  if (!pageReady.value || locationMode.value !== 'curated') return
  loadViewpoints()
  syncUrl()
})
watch([viewpointId, locationId, currentDate], () => {
  if (!pageReady.value) return
  syncUrl()
})
watch(month, () => {
  if (!pageReady.value) return
  loadSession()
})
watch([locationMode, spotId, viewpointId, locationId, currentDate, poiLat, poiLng], () => {
  if (!pageReady.value) return
  loadSession()
})

onMounted(async () => {
  parseUrlParams()
  await loadSpots()
  await loadViewpoints()
  await loadMyLocations()
  syncUrl()
  pageReady.value = true
  await loadSession()
})

function onKeydown(e: KeyboardEvent) {
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
  if (e.key === '1') applyLabel('none')
  if (e.key === '2') applyLabel('partial')
  if (e.key === '3') applyLabel('full')
  if (e.key === 'ArrowLeft') shiftDate(-1)
  if (e.key === 'ArrowRight') shiftDate(1)
}

onMounted(() => window.addEventListener('keydown', onKeydown))
</script>

<template>
  <div class="min-h-screen bg-slate-950 p-4 text-slate-100 md:p-6">
    <div class="mx-auto max-w-6xl space-y-4">
      <div class="rounded-xl border border-sky-800/60 bg-sky-950/40 p-4">
        <div class="text-lg font-semibold text-sky-100">云海标注 · 开放贡献</div>
        <div class="mt-1 text-sm text-slate-400">
          标注日出窗口（03:00–07:00）实际云海情况，审核通过后将纳入模型训练。匿名 ID：…{{ contributorIdShort() }}
        </div>
        <div v-if="stats && !adminMode" class="mt-2 text-xs text-slate-400">
          今日已标注 {{ stats.labels_today }}/{{ stats.daily_cap }} · 累计通过 {{ stats.labels_approved }} · 待审 {{ stats.labels_pending }}
        </div>
      </div>

      <div v-if="adminMode" class="space-y-3">
        <div class="flex flex-wrap items-end gap-3 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <div>
            <div class="mb-1 text-xs text-slate-400">Admin Token</div>
            <n-input v-model:value="token" type="password" placeholder="X-Cloudsea-Token" style="width: 260px" />
          </div>
          <n-button @click="saveToken">保存 Token</n-button>
          <div class="text-xs text-amber-400">Admin 模式：审核社区贡献、重训模型、管理精选落库</div>
        </div>
        <AdminReviewPanel v-if="token" :token="token" @open-label="openReviewItem" @refreshed="loadSession" />
      </div>

      <div class="flex flex-wrap items-end gap-3 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <div class="w-full text-sm font-medium text-slate-300">切换标注点位</div>
        <div>
          <div class="mb-1 text-xs text-slate-400">点位类型</div>
          <n-radio-group
            :value="locationMode"
            size="small"
            @update:value="onLocationModeChange"
          >
            <n-radio-button value="curated">精选景区</n-radio-button>
            <n-radio-button value="community">社区 / POI</n-radio-button>
          </n-radio-group>
        </div>

        <template v-if="locationMode === 'curated'">
          <div>
            <div class="mb-1 text-xs text-slate-400">景区</div>
            <n-select
              v-model:value="spotId"
              filterable
              placeholder="选择景区"
              :options="spotOptions"
              style="width: 200px"
            />
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-400">观景点</div>
            <n-select
              v-model:value="viewpointId"
              filterable
              placeholder="选择观景点"
              :options="viewpointOptions"
              style="width: 180px"
            />
          </div>
        </template>

        <template v-else>
          <div v-if="poiLat != null && !locationId">
            <div class="mb-1 text-xs text-slate-400">POI 坐标</div>
            <div class="rounded border border-slate-700 bg-slate-950/60 px-3 py-2 text-sm text-slate-300">
              {{ poiName || '自定义' }}（{{ poiLat.toFixed(4) }}, {{ poiLng?.toFixed(4) }}）
            </div>
          </div>
          <div v-else>
            <div class="mb-1 text-xs text-slate-400">社区点位</div>
            <n-select
              v-model:value="locationId"
              filterable
              placeholder="选择已贡献的点位"
              :options="communityOptions"
              style="width: 220px"
            />
          </div>
          <n-button tag="a" href="/" quaternary size="small">去主页选新 POI</n-button>
        </template>

        <div class="flex items-center gap-2">
          <n-button @click="shiftDate(-1)">◀</n-button>
          <n-date-picker v-model:formatted-value="currentDate" value-format="yyyy-MM-dd" type="date" />
          <n-button @click="shiftDate(1)">▶</n-button>
        </div>

        <div class="w-full text-xs text-slate-500">
          当前：{{ locationTitle }}
          <span v-if="linkedCommunityId"> · 标注同步自社区点 {{ linkedCommunityId }}</span>
        </div>
      </div>

      <n-spin :show="loading">
        <div v-if="session" class="grid gap-4 lg:grid-cols-[1fr_320px]">
          <div class="space-y-4">
            <div class="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
              <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div class="text-lg font-semibold">{{ locationTitle }} · {{ currentDate }}</div>
                  <div class="text-sm text-slate-400">
                    日出窗口 03:00–07:00
                    <template v-if="windowScoreSummary">
                      · 云海峰值 {{ windowScoreSummary.peakCloud.cloudsea.probability }}%
                      · 综合 {{ windowScoreSummary.peakCombined.scenario.combined_score }}
                      · {{ windowScoreSummary.peakCombined.scenario.label }}
                    </template>
                  </div>
                  <div v-if="dataSourceLabel" class="mt-1 text-xs text-slate-500">数据源：{{ dataSourceLabel }}</div>
                  <div v-if="session.label?.review_status === 'pending'" class="mt-1 text-xs text-sky-400">当前标注待审核</div>
                </div>
                <div class="flex gap-2">
                  <n-button :type="selectedStatus === 'none' ? 'error' : 'default'" @click="applyLabel('none')">无云海 (1)</n-button>
                  <n-button :type="selectedStatus === 'partial' ? 'warning' : 'default'" @click="applyLabel('partial')">部分 (2)</n-button>
                  <n-button :type="selectedStatus === 'full' ? 'success' : 'default'" @click="applyLabel('full')">完整 (3)</n-button>
                </div>
              </div>
              <n-input v-model:value="notes" type="textarea" placeholder="备注（可选）" :rows="2" class="mb-4" />
              <div class="overflow-x-auto">
                <table class="w-full text-sm">
                  <thead class="text-slate-400">
                    <tr>
                      <th class="px-2 py-1 text-left">时间</th>
                      <th class="px-2 py-1 text-right">低/中/高云</th>
                      <th class="px-2 py-1 text-right">能见度</th>
                      <th class="px-2 py-1 text-right">RH/RH850/RH700</th>
                      <th class="px-2 py-1 text-right">逆温ΔT</th>
                      <th class="px-2 py-1 text-right">风速</th>
                      <th class="px-2 py-1 text-right">云海%</th>
                      <th class="px-2 py-1 text-right">综合</th>
                      <th class="px-2 py-1 text-left">场景</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in session.raw_meteo" :key="String(row.time)" class="border-t border-slate-800">
                      <td class="px-2 py-1">{{ String(row.time).slice(11, 16) }}</td>
                      <td class="px-2 py-1 text-right">{{ row.cloud_low }}/{{ row.cloud_mid }}/{{ row.cloud_high ?? '—' }}%</td>
                      <td class="px-2 py-1 text-right">{{ row.visibility }}m</td>
                      <td class="px-2 py-1 text-right">{{ row.rh }}/{{ row.rh_850 }}/{{ row.rh_700 ?? '—' }}%</td>
                      <td class="px-2 py-1 text-right">{{ row.inversion != null ? `${Number(row.inversion).toFixed(1)}°C` : '—' }}</td>
                      <td class="px-2 py-1 text-right">{{ row.wind }}</td>
                      <td class="px-2 py-1 text-right">{{ session.hours.find((h) => h.time === row.time)?.cloudsea.probability ?? '—' }}</td>
                      <td class="px-2 py-1 text-right">{{ session.hours.find((h) => h.time === row.time)?.scenario.combined_score ?? '—' }}</td>
                      <td class="px-2 py-1">{{ session.hours.find((h) => h.time === row.time)?.scenario.label ?? '—' }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div v-if="accuracy && adminMode" class="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
              <div class="mb-2 font-semibold">回测准确率（已标注日）</div>
              <div class="text-sm text-slate-300">
                {{ accuracy.correct }}/{{ accuracy.total }}
                <span v-if="accuracy.accuracy != null">· {{ (accuracy.accuracy * 100).toFixed(1) }}%</span>
              </div>
            </div>
          </div>

          <div class="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <div class="mb-3 font-semibold">{{ month }} 标注日历</div>
            <div class="grid grid-cols-7 gap-1 text-center text-[11px]">
              <button
                v-for="d in buildMonthDays()"
                :key="d"
                class="rounded border px-1 py-2"
                :class="dayClass(d)"
                @click="currentDate = d"
              >
                {{ d.slice(8) }}
              </button>
            </div>
            <div class="mt-4 space-y-1 text-xs text-slate-400">
              <div><span class="inline-block h-2 w-2 rounded bg-emerald-500"></span> 完整云海</div>
              <div><span class="inline-block h-2 w-2 rounded bg-amber-500"></span> 部分云海</div>
              <div><span class="inline-block h-2 w-2 rounded bg-slate-500"></span> 无云海</div>
              <div><span class="inline-block h-2 w-2 rounded ring-1 ring-sky-500/50 bg-emerald-600/30"></span> 待审核</div>
            </div>
            <div class="mt-4 text-[11px] leading-relaxed text-slate-500">
              为限制恶意刷标注，浏览器会保存匿名 ID。清除站点数据后将生成新 ID。审核通过的标注将纳入定期模型训练。
            </div>
            <n-button tag="a" href="/" quaternary size="tiny" class="mt-3">← 返回预测主页</n-button>
          </div>
        </div>
      </n-spin>
    </div>
  </div>
</template>
