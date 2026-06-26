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
const tempChartRef = ref<HTMLElement | null>(null)
const spreadChartRef = ref<HTMLElement | null>(null)
let rhChart: echarts.ECharts | null = null
let cloudChart: echarts.ECharts | null = null
let tempChart: echarts.ECharts | null = null
let spreadChart: echarts.ECharts | null = null

function formatAccessTime(iso: string) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function outcomeMark(ok: number | null | undefined) {
  if (ok == null) return '—'
  return ok ? '✓' : '✗'
}

function isScheduledSource(source: string | null | undefined) {
  return source === 'scheduled' || source === 'scheduled_watch'
}

function sourceLabel(source: string | null | undefined) {
  if (!source) return ''
  if (isScheduledSource(source)) return '系统'
  if (source === 'label') return '标注'
  if (source === 'main') return '主页'
  return source
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

const plainSummary = computed(() => {
  const d = detail.value
  if (!d?.segments?.length) return ''
  const dawn = d.segments.find((s) => s.segment === 'dawn')
  const parts: string[] = []
  if (d.peak_cloudsea_prob != null) {
    parts.push(`这次查看时系统给出云海概率 ${d.peak_cloudsea_prob}%`)
  }
  if (dawn?.rh_forecast != null && dawn.rh_actual != null && dawn.rh_delta != null) {
    const dir = dawn.rh_delta > 2 ? '偏高' : dawn.rh_delta < -2 ? '偏低' : '接近'
    parts.push(`日出段湿度预报${dir}（预报 ${dawn.rh_forecast}% → 实况 ${dawn.rh_actual}%）`)
  } else if (dawn?.rh_forecast != null && dawn.rh_actual == null) {
    parts.push('日出段实况湿度尚未回填，暂无法对比')
  }
  if (d.direction_ok === 1) parts.push('与您的标注方向一致')
  if (d.direction_ok === 0) parts.push('与您的标注方向不一致，可重点看下方差异')
  return parts.join('；')
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

function renderDualChart(
  el: HTMLElement | null,
  existing: echarts.ECharts | null,
  title: [string, string],
  forecastData: Array<number | null>,
  actualData: Array<number | null>,
  colors: [string, string],
  yAxis?: { min?: number; max?: number; name?: string },
): echarts.ECharts | null {
  if (!el) return existing
  const chart = existing ?? echarts.init(el, undefined, { renderer: 'canvas' })
  const x = (detail.value?.curve_points ?? []).map((p) => p.label)
  chart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 44, right: 12, top: 32, bottom: 28 },
    tooltip: { trigger: 'axis' },
    legend: { data: title, textStyle: { color: '#94a3b8', fontSize: 11 }, top: 0 },
    xAxis: { type: 'category', data: x, axisLabel: { color: '#64748b', fontSize: 10 } },
    yAxis: {
      type: 'value',
      min: yAxis?.min,
      max: yAxis?.max,
      name: yAxis?.name,
      nameTextStyle: { color: '#64748b', fontSize: 10 },
      axisLabel: { color: '#64748b', fontSize: 10 },
    },
    series: [
      lineSeries(title[0], forecastData, colors[0], true),
      lineSeries(title[1], actualData, colors[1]),
    ],
  })
  return chart
}

function renderCharts() {
  const pts = detail.value?.curve_points ?? []
  if (!pts.length) return
  rhChart = renderDualChart(
    rhChartRef.value,
    rhChart,
    ['预报湿度', '实况湿度'],
    pts.map((p) => (p.forecast_rh != null ? Number(p.forecast_rh) : null)),
    pts.map((p) => (p.actual_rh != null ? Number(p.actual_rh) : null)),
    ['#38bdf8', '#34d399'],
    { min: 0, max: 100 },
  )
  cloudChart = renderDualChart(
    cloudChartRef.value,
    cloudChart,
    ['预报低云', '实况低云'],
    pts.map((p) => (p.forecast_cloud_low != null ? Number(p.forecast_cloud_low) : null)),
    pts.map((p) => (p.actual_cloud_low != null ? Number(p.actual_cloud_low) : null)),
    ['#a78bfa', '#fbbf24'],
    { min: 0, max: 100 },
  )
  tempChart = renderDualChart(
    tempChartRef.value,
    tempChart,
    ['预报气温', '实况气温'],
    pts.map((p) => (p.forecast_temp != null ? Number(p.forecast_temp) : null)),
    pts.map((p) => (p.actual_temp != null ? Number(p.actual_temp) : null)),
    ['#fb7185', '#f97316'],
    { name: '°C' },
  )
  spreadChart = renderDualChart(
    spreadChartRef.value,
    spreadChart,
    ['预报温差', '实况温差'],
    pts.map((p) => (p.forecast_spread != null ? Number(p.forecast_spread) : null)),
    pts.map((p) => (p.actual_spread != null ? Number(p.actual_spread) : null)),
    ['#22d3ee', '#2dd4bf'],
    { name: '°C', min: 0, max: 15 },
  )
}

function onResize() {
  rhChart?.resize()
  cloudChart?.resize()
  tempChart?.resize()
  spreadChart?.resize()
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
  tempChart?.dispose()
  spreadChart?.dispose()
})

window.addEventListener('resize', onResize)
</script>

<template>
  <div v-if="history" class="rounded-xl border border-slate-800 bg-slate-900/60 p-4 space-y-4">
    <div
      v-if="history.scheduled_summary?.banner"
      class="rounded-lg border px-3 py-2 text-xs"
      :class="
        history.scheduled_summary.needs_label_attention
          ? 'border-amber-500/40 bg-amber-950/30 text-amber-100'
          : 'border-sky-500/30 bg-sky-950/30 text-sky-100'
      "
    >
      {{ history.scheduled_summary.banner }}
    </div>

    <div class="rounded-lg border border-slate-700/60 bg-slate-950/40 px-3 py-2 text-xs text-slate-400 leading-relaxed">
      <div class="font-medium text-slate-300 mb-1">怎么看这张图？</div>
      虚线 = 您（或系统）查看<strong class="text-slate-300">当时</strong>能看到的天气预报；实线 = 日出结束后回填的<strong class="text-slate-300">真实气象</strong>。两者越接近，说明预报越准、预测越可信。
    </div>

    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="font-semibold">历史预测访问</div>
      <div class="text-xs text-slate-400">
        标注 {{ history.label?.status || '未标注' }}
        · 访问 {{ history.access_count }} 次
        <template v-if="history.scheduled_summary?.scheduled_count">
          · 系统 {{ history.scheduled_summary.scheduled_count }} 次
        </template>
        <template v-if="history.outcome_count">
          · 正确 {{ history.correct_count }}/{{ history.outcome_count }}
        </template>
      </div>
    </div>

    <div v-if="!history.entries.length" class="text-xs text-slate-500">
      暂无预测快照。启用定时 watcher 后，系统将在气象变化时自动积累；用户访问预测页也会写入。
    </div>

    <template v-else>
      <div class="overflow-x-auto">
        <table class="w-full text-xs">
          <thead class="text-slate-400">
            <tr>
              <th class="px-2 py-1 text-left">访问时间</th>
              <th class="px-2 py-1 text-left">来源</th>
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
              <td class="px-2 py-1">
                <span
                  v-if="isScheduledSource(row.page_source)"
                  class="rounded bg-sky-900/60 px-1.5 py-0.5 text-[10px] text-sky-200"
                >
                  {{ sourceLabel(row.page_source) }}
                </span>
                <span v-else class="text-slate-400">{{ sourceLabel(row.page_source) || '—' }}</span>
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
          </div>

          <div
            v-if="detail.actual_meteo_status?.message"
            class="rounded-lg border px-3 py-2 text-xs"
            :class="{
              'border-emerald-500/30 bg-emerald-950/20 text-emerald-100': detail.actual_meteo_status.level === 'ok',
              'border-amber-500/40 bg-amber-950/30 text-amber-100':
                detail.actual_meteo_status.level === 'pending' || detail.actual_meteo_status.level === 'partial',
              'border-slate-600 bg-slate-900/60 text-slate-400': detail.actual_meteo_status.level === 'missing',
            }"
          >
            {{ detail.actual_meteo_status.message }}
            <span v-if="detail.actual_meteo_status.actual_hours != null" class="ml-1 opacity-80">
              （实况 {{ detail.actual_meteo_status.actual_hours }}/{{ detail.actual_meteo_status.expected_hours }} 小时）
            </span>
          </div>

          <div v-if="plainSummary" class="rounded-lg border border-slate-700/80 bg-slate-950/50 px-3 py-2 text-xs text-slate-200">
            {{ plainSummary }}
          </div>
          <div v-if="detail.diagnosis?.summary" class="rounded-lg border border-slate-700/80 bg-slate-950/50 px-3 py-2 text-xs text-slate-300">
            {{ detail.diagnosis.summary }}
          </div>

          <div class="grid gap-3 lg:grid-cols-2">
            <div class="rounded-lg border border-slate-800 bg-slate-950/40 p-2">
              <div class="mb-1 text-[11px] text-slate-500">空气湿度 %（虚线=预报 · 实线=实况）</div>
              <div ref="rhChartRef" class="h-40 w-full" />
            </div>
            <div class="rounded-lg border border-slate-800 bg-slate-950/40 p-2">
              <div class="mb-1 text-[11px] text-slate-500">低云量 %（虚线=预报 · 实线=实况）</div>
              <div ref="cloudChartRef" class="h-40 w-full" />
            </div>
            <div class="rounded-lg border border-slate-800 bg-slate-950/40 p-2">
              <div class="mb-1 text-[11px] text-slate-500">气温 °C（虚线=预报 · 实线=实况）</div>
              <div ref="tempChartRef" class="h-40 w-full" />
            </div>
            <div class="rounded-lg border border-slate-800 bg-slate-950/40 p-2">
              <div class="mb-1 text-[11px] text-slate-500">气温－露点（温差，°C；越小越接近饱和/易起雾）</div>
              <div ref="spreadChartRef" class="h-40 w-full" />
            </div>
          </div>

          <div v-if="detail.segments?.length" class="overflow-x-auto">
            <div class="mb-1 text-[11px] text-slate-500">分时段对比（前夜 / 夜间 / 日出）</div>
            <table class="w-full text-xs">
              <thead class="text-slate-400">
                <tr>
                  <th class="px-2 py-1 text-left">时段</th>
                  <th class="px-2 py-1 text-right">RH 预报</th>
                  <th class="px-2 py-1 text-right">RH 实况</th>
                  <th class="px-2 py-1 text-right">ΔRH</th>
                  <th class="px-2 py-1 text-right">气温 预报</th>
                  <th class="px-2 py-1 text-right">气温 实况</th>
                  <th class="px-2 py-1 text-right">温差 预报</th>
                  <th class="px-2 py-1 text-right">温差 实况</th>
                  <th class="px-2 py-1 text-right">低云 预报</th>
                  <th class="px-2 py-1 text-right">低云 实况</th>
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
                  <td class="px-2 py-1 text-right">{{ seg.temp_forecast ?? '—' }}</td>
                  <td class="px-2 py-1 text-right">{{ seg.temp_actual ?? '—' }}</td>
                  <td class="px-2 py-1 text-right">{{ seg.spread_forecast ?? '—' }}</td>
                  <td class="px-2 py-1 text-right">{{ seg.spread_actual ?? '—' }}</td>
                  <td class="px-2 py-1 text-right">{{ seg.cloud_low_forecast ?? '—' }}%</td>
                  <td class="px-2 py-1 text-right">{{ seg.cloud_low_actual ?? '—' }}%</td>
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
