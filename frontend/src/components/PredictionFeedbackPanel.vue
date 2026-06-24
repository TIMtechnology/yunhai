<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { PredictionHistory, PredictionSnapshotDetail } from '../services/cloudseaLabel'
import { fetchPredictionSnapshotDetail } from '../services/cloudseaLabel'

const props = defineProps<{
  history: PredictionHistory | null
}>()

const selectedLogId = ref<number | null>(null)
const detail = ref<PredictionSnapshotDetail | null>(null)
const detailLoading = ref(false)
const rhChartRef = ref<HTMLElement | null>(null)
const cloudChartRef = ref<HTMLElement | null>(null)
let rhChart: echarts.ECharts | null = null
let cloudChart: echarts.ECharts | null = null

function formatAccessTime(iso: string) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function outcomeMark(ok: number | null | undefined) {
  if (ok == null) return '—'
  return ok ? '✓' : '✗'
}

function resolveLogId(row: { log_id?: number; id: number | string }): number {
  if (row.log_id) return row.log_id
  const raw = String(row.id)
  return Number(raw.split(':')[0])
}

const defaultLogId = computed(() => {
  if (!props.history?.entries.length) return null
  const row = props.history.entries.find((e) => !e.same_forecast) ?? props.history.entries[0]
  return resolveLogId(row)
})

async function loadDetail(logId: number) {
  selectedLogId.value = logId
  detailLoading.value = true
  try {
    detail.value = await fetchPredictionSnapshotDetail(logId)
    await nextTick()
    renderCharts()
  } catch {
    detail.value = null
  } finally {
    detailLoading.value = false
  }
}

function lineSeries(name: string, data: Array<number | null>, color: string, dashed = false) {
  return {
    name,
    type: 'line',
    smooth: true,
    showSymbol: false,
    connectNulls: false,
    lineStyle: { width: 2, type: dashed ? 'dashed' : 'solid', color },
    itemStyle: { color },
    data,
  }
}

function renderCharts() {
  const pts = detail.value?.curve_points ?? []
  if (!pts.length) return
  const x = pts.map((p) => p.label)

  if (rhChartRef.value) {
    if (!rhChart) rhChart = echarts.init(rhChartRef.value, undefined, { renderer: 'canvas' })
    rhChart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 40, right: 12, top: 28, bottom: 28 },
      tooltip: { trigger: 'axis' },
      legend: { data: ['预报 RH', '实况 RH'], textStyle: { color: '#94a3b8', fontSize: 11 }, top: 0 },
      xAxis: { type: 'category', data: x, axisLabel: { color: '#64748b', fontSize: 10 } },
      yAxis: { type: 'value', min: 0, max: 100, axisLabel: { color: '#64748b', fontSize: 10 } },
      series: [
        lineSeries('预报 RH', pts.map((p) => (p.forecast_rh != null ? Number(p.forecast_rh) : null)), '#38bdf8', true),
        lineSeries('实况 RH', pts.map((p) => (p.actual_rh != null ? Number(p.actual_rh) : null)), '#34d399'),
      ],
    })
  }

  if (cloudChartRef.value) {
    if (!cloudChart) cloudChart = echarts.init(cloudChartRef.value, undefined, { renderer: 'canvas' })
    cloudChart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 40, right: 12, top: 28, bottom: 28 },
      tooltip: { trigger: 'axis' },
      legend: { data: ['预报低云', '实况低云'], textStyle: { color: '#94a3b8', fontSize: 11 }, top: 0 },
      xAxis: { type: 'category', data: x, axisLabel: { color: '#64748b', fontSize: 10 } },
      yAxis: { type: 'value', min: 0, max: 100, axisLabel: { color: '#64748b', fontSize: 10 } },
      series: [
        lineSeries('预报低云', pts.map((p) => (p.forecast_cloud_low != null ? Number(p.forecast_cloud_low) : null)), '#a78bfa', true),
        lineSeries('实况低云', pts.map((p) => (p.actual_cloud_low != null ? Number(p.actual_cloud_low) : null)), '#fbbf24'),
      ],
    })
  }
}

function onResize() {
  rhChart?.resize()
  cloudChart?.resize()
}

watch(
  () => props.history,
  (h) => {
    if (!h?.entries.length) {
      detail.value = null
      selectedLogId.value = null
      return
    }
    const pick = defaultLogId.value
    if (pick) loadDetail(pick)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  rhChart?.dispose()
  cloudChart?.dispose()
})

window.addEventListener('resize', onResize)
</script>

<template>
  <div v-if="history" class="rounded-xl border border-slate-800 bg-slate-900/60 p-4 space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="font-semibold">历史预测访问</div>
      <div class="text-xs text-slate-400">
        标注 {{ history.label?.status || '未标注' }}
        · 访问 {{ history.access_count }} 次
        <template v-if="history.snapshot_count != null && history.snapshot_count < history.access_count">
          · {{ history.snapshot_count }} 条预报快照
        </template>
        <template v-if="history.outcome_count">
          · 正确 {{ history.correct_count }}/{{ history.outcome_count }}
        </template>
      </div>
    </div>

    <div v-if="!history.entries.length" class="text-xs text-slate-500">
      暂无用户访问快照（上线后将随 /api/predict 自动积累）
    </div>

    <template v-else>
      <div class="overflow-x-auto">
        <table class="w-full text-xs">
          <thead class="text-slate-400">
            <tr>
              <th class="px-2 py-1 text-left">访问时间</th>
              <th class="px-2 py-1 text-right">P(云海)</th>
              <th class="px-2 py-1 text-right">lead(h)</th>
              <th class="px-2 py-1 text-center">结果</th>
              <th class="px-2 py-1 text-left">诊断</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in history.entries"
              :key="String(row.id)"
              class="border-t border-slate-800 cursor-pointer transition-colors"
              :class="selectedLogId === resolveLogId(row) ? 'bg-sky-950/50' : 'hover:bg-slate-800/40'"
              @click="loadDetail(resolveLogId(row))"
            >
              <td class="px-2 py-1">
                {{ formatAccessTime(row.created_at) }}
                <span v-if="row.same_forecast" class="ml-1 text-slate-500">同预报</span>
              </td>
              <td class="px-2 py-1 text-right">{{ row.peak_cloudsea_prob ?? '—' }}%</td>
              <td class="px-2 py-1 text-right">{{ row.lead_hours_to_dawn?.toFixed(1) ?? '—' }}</td>
              <td class="px-2 py-1 text-center">{{ outcomeMark(row.direction_ok) }}</td>
              <td class="px-2 py-1 text-slate-400">{{ row.diagnosis?.summary || row.diagnosis?.tags?.join(', ') || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <n-spin :show="detailLoading">
        <div v-if="detail" class="space-y-3 border-t border-slate-800 pt-3">
          <div class="text-xs text-slate-400">
            选中快照 #{{ detail.id }} · {{ formatAccessTime(detail.created_at) }}
            · P={{ detail.peak_cloudsea_prob ?? '—' }}%
            · 标注 {{ detail.label_status || '—' }}
            <span v-if="detail.diagnosis?.tags?.length" class="ml-2 text-amber-300/90">
              {{ detail.diagnosis.tags.join(' · ') }}
            </span>
          </div>
          <div v-if="detail.diagnosis?.summary" class="rounded-lg border border-slate-700/80 bg-slate-950/50 px-3 py-2 text-xs text-slate-300">
            {{ detail.diagnosis.summary }}
          </div>

          <div class="grid gap-3 lg:grid-cols-2">
            <div class="rounded-lg border border-slate-800 bg-slate-950/40 p-2">
              <div class="mb-1 text-[11px] text-slate-500">RH 演变（虚线=访问时刻预报 · 实线=事后实况）</div>
              <div ref="rhChartRef" class="h-44 w-full" />
            </div>
            <div class="rounded-lg border border-slate-800 bg-slate-950/40 p-2">
              <div class="mb-1 text-[11px] text-slate-500">低云量 %（虚线=预报 · 实线=实况）</div>
              <div ref="cloudChartRef" class="h-44 w-full" />
            </div>
          </div>

          <div v-if="detail.segments?.length" class="overflow-x-auto">
            <div class="mb-1 text-[11px] text-slate-500">分段差异（evening / night / dawn）</div>
            <table class="w-full text-xs">
              <thead class="text-slate-400">
                <tr>
                  <th class="px-2 py-1 text-left">时段</th>
                  <th class="px-2 py-1 text-right">RH 预报</th>
                  <th class="px-2 py-1 text-right">RH 实况</th>
                  <th class="px-2 py-1 text-right">ΔRH</th>
                  <th class="px-2 py-1 text-right">低云 预报</th>
                  <th class="px-2 py-1 text-right">低云 实况</th>
                  <th class="px-2 py-1 text-right">Δ低云</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="seg in detail.segments" :key="seg.segment" class="border-t border-slate-800">
                  <td class="px-2 py-1">{{ seg.label }}</td>
                  <td class="px-2 py-1 text-right">{{ seg.rh_forecast ?? '—' }}%</td>
                  <td class="px-2 py-1 text-right">{{ seg.rh_actual ?? '—' }}%</td>
                  <td class="px-2 py-1 text-right" :class="seg.rh_delta != null && seg.rh_delta < -3 ? 'text-red-300' : ''">
                    {{ seg.rh_delta != null ? (seg.rh_delta > 0 ? '+' : '') + seg.rh_delta : '—' }}
                  </td>
                  <td class="px-2 py-1 text-right">{{ seg.cloud_low_forecast ?? '—' }}%</td>
                  <td class="px-2 py-1 text-right">{{ seg.cloud_low_actual ?? '—' }}%</td>
                  <td class="px-2 py-1 text-right" :class="seg.cloud_low_delta != null && seg.cloud_low_delta > 8 ? 'text-red-300' : ''">
                    {{ seg.cloud_low_delta != null ? (seg.cloud_low_delta > 0 ? '+' : '') + seg.cloud_low_delta : '—' }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <div v-else-if="!detailLoading" class="text-xs text-slate-500 pt-2">点击上方行查看预报 vs 实况曲线</div>
      </n-spin>
    </template>
  </div>
</template>
