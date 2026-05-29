<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import type { LabelStatus } from '../services/cloudseaLabel'
import {
  fetchAccuracy,
  fetchCalendar,
  fetchLabelSession,
  fetchSpotDetail,
  fetchSpots,
  saveLabel,
} from '../services/cloudseaLabel'

const TOKEN_KEY = 'cloudsea_admin_token'

const message = useMessage()
const token = ref(localStorage.getItem(TOKEN_KEY) || '')
const spots = ref<Array<{ id: string; name: string }>>([])
const viewpoints = ref<Array<{ id: string; name: string }>>([])
const spotId = ref('wunvshan')
const viewpointId = ref('dianjiangtai')
const currentDate = ref('2026-05-29')
const notes = ref('')
const loading = ref(false)
const session = ref<Awaited<ReturnType<typeof fetchLabelSession>> | null>(null)
const calendar = ref<Record<string, string>>({})
const accuracy = ref<{ total: number; correct: number; accuracy: number | null; details: Array<Record<string, unknown>> } | null>(null)
const selectedStatus = ref<LabelStatus | null>(null)

const month = computed(() => currentDate.value.slice(0, 7))

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

async function loadSession() {
  if (!token.value) return
  loading.value = true
  try {
    session.value = await fetchLabelSession(token.value, spotId.value, viewpointId.value, currentDate.value)
    selectedStatus.value = (session.value.label?.status as LabelStatus) || null
    notes.value = session.value.label?.notes || ''
    const cal = await fetchCalendar(token.value, spotId.value, viewpointId.value, month.value)
    calendar.value = Object.fromEntries(cal.labels.map((x) => [x.date, x.status]))
    accuracy.value = await fetchAccuracy(token.value, spotId.value, viewpointId.value)
  } catch (err) {
    message.error(String(err))
  } finally {
    loading.value = false
  }
}

async function applyLabel(status: LabelStatus) {
  if (!token.value) {
    message.warning('请先填写 Admin Token')
    return
  }
  selectedStatus.value = status
  try {
    await saveLabel(token.value, {
      spot_id: spotId.value,
      viewpoint_id: viewpointId.value,
      date: currentDate.value,
      status,
      notes: notes.value,
    })
    message.success(`已保存 ${currentDate.value}`)
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

function dayClass(date: string) {
  const st = calendar.value[date]
  if (st === 'full') return 'bg-emerald-600/30 border-emerald-500'
  if (st === 'partial') return 'bg-amber-600/30 border-amber-500'
  if (st === 'none') return 'bg-slate-600/40 border-slate-500'
  return 'bg-slate-900 border-slate-700'
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

watch([spotId], loadViewpoints)
watch([token, spotId, viewpointId, currentDate], loadSession)

onMounted(async () => {
  await loadSpots()
  await loadViewpoints()
  if (token.value) await loadSession()
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
  <div class="min-h-screen p-4 md:p-6">
        <div class="mx-auto max-w-6xl space-y-4">
          <div class="flex flex-wrap items-end gap-3 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <div>
              <div class="mb-1 text-xs text-slate-400">Admin Token</div>
              <n-input v-model:value="token" type="password" placeholder="X-Cloudsea-Token" style="width: 260px" />
            </div>
            <n-button @click="saveToken">保存 Token</n-button>
            <div>
              <div class="mb-1 text-xs text-slate-400">景区</div>
              <n-select v-model:value="spotId" :options="spots.map((s) => ({ label: s.name, value: s.id }))" style="width: 180px" />
            </div>
            <div>
              <div class="mb-1 text-xs text-slate-400">观景点</div>
              <n-select v-model:value="viewpointId" :options="viewpoints.map((v) => ({ label: v.name, value: v.id }))" style="width: 160px" />
            </div>
            <div class="flex items-center gap-2">
              <n-button @click="shiftDate(-1)">◀</n-button>
              <n-date-picker v-model:formatted-value="currentDate" value-format="yyyy-MM-dd" type="date" />
              <n-button @click="shiftDate(1)">▶</n-button>
            </div>
          </div>

          <n-spin :show="loading">
            <div v-if="session" class="grid gap-4 lg:grid-cols-[1fr_320px]">
              <div class="space-y-4">
                <div class="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
                  <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <div class="text-lg font-semibold">{{ currentDate }} 日出窗口 03:00–07:00</div>
                      <div class="text-sm text-slate-400">
                        模型峰值：
                        {{ session.sunrise_window_summary?.max_cloudsea_prob ?? '—' }}%
                        · {{ session.sunrise_window_summary?.scenario ?? '—' }}
                      </div>
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
                          <th class="px-2 py-1 text-right">模型%</th>
                          <th class="px-2 py-1 text-left">场景</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="row in session.raw_meteo" :key="String(row.time)" class="border-t border-slate-800">
                          <td class="px-2 py-1">{{ String(row.time).slice(11, 16) }}</td>
                          <td class="px-2 py-1 text-right">
                            {{ row.cloud_low }}/{{ row.cloud_mid }}/{{ row.cloud_high ?? '—' }}%
                          </td>
                          <td class="px-2 py-1 text-right">{{ row.visibility }}m</td>
                          <td class="px-2 py-1 text-right">
                            {{ row.rh }}/{{ row.rh_850 }}/{{ row.rh_700 ?? '—' }}%
                          </td>
                          <td class="px-2 py-1 text-right">
                            {{ row.inversion != null ? `${Number(row.inversion).toFixed(1)}°C` : '—' }}
                          </td>
                          <td class="px-2 py-1 text-right">{{ row.wind }}</td>
                          <td class="px-2 py-1 text-right">
                            {{ session.hours.find((h) => h.time === row.time)?.cloudsea.probability ?? '—' }}
                          </td>
                          <td class="px-2 py-1">
                            {{ session.hours.find((h) => h.time === row.time)?.scenario.label ?? '—' }}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>

                <div v-if="accuracy" class="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
                  <div class="mb-2 font-semibold">回测准确率（已标注日）</div>
                  <div class="text-sm text-slate-300">
                    {{ accuracy.correct }}/{{ accuracy.total }}
                    <span v-if="accuracy.accuracy != null">· {{ (accuracy.accuracy * 100).toFixed(1) }}%</span>
                  </div>
                  <div class="mt-2 space-y-1 text-xs">
                    <div v-for="d in accuracy.details" :key="String(d.date)" class="flex justify-between">
                      <span>{{ d.date }} · 标注 {{ d.status }}</span>
                      <span :class="d.correct ? 'text-emerald-400' : 'text-red-400'">
                        模型 {{ d.peak_prob }}% {{ d.correct ? '✓' : '✗' }}
                      </span>
                    </div>
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
                  <div><span class="inline-block h-2 w-2 rounded border border-slate-600"></span> 未标注</div>
                </div>
              </div>
            </div>
          </n-spin>
        </div>
      </div>
</template>
