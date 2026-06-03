<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { useAppStore } from '../stores/app'
import { fetchMeteoProfile, type MeteoProfileResponse } from '../services/api'

const store = useAppStore()
const expanded = ref(false)
const loading = ref(false)
const error = ref('')
const profile = ref<MeteoProfileResponse | null>(null)
const chartRef = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null

const day = computed(() => store.selectedDay)
const loc = computed(() => store.prediction?.location)
const labels = computed(() =>
  store.dayHours.map((h) => {
    const d = new Date(h.time)
    return `${String(d.getHours()).padStart(2, '0')}:00`
  }),
)

async function loadProfile() {
  if (!expanded.value || !day.value || !loc.value) return
  loading.value = true
  error.value = ''
  try {
    profile.value = await fetchMeteoProfile({
      lat: loc.value.lat,
      lng: loc.value.lng,
      date: day.value.date,
      elevation: loc.value.elevation,
    })
    await nextTick()
    renderChart()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function windArrow(deg?: number) {
  if (deg == null) return '·'
  // Open-Meteo wind_direction_10m is the direction wind comes from; display where it blows to.
  const to = (deg + 180) % 360
  const arrows = ['↓', '↙', '←', '↖', '↑', '↗', '→', '↘']
  return arrows[Math.round((((to % 360) + 360) % 360) / 45) % 8]
}

function renderChart() {
  if (!chartRef.value || !store.dayHours.length) return
  if (!chart) chart = echarts.init(chartRef.value, undefined, { renderer: 'canvas' })

  const hours = store.dayHours
  const x = labels.value
  const heatData: [number, number, number][] = []
  const heightLabels: string[] = []
  const levelMap = new Map<number, number>()
  for (const h of profile.value?.hours ?? []) {
    for (const level of h.levels) {
      const km = Math.max(0, Math.round(level.height_m_asl / 100) / 10)
      if (!levelMap.has(km)) levelMap.set(km, levelMap.size)
    }
  }
  const heights = [...levelMap.keys()].sort((a, b) => a - b).slice(0, 14)
  heights.forEach((h) => heightLabels.push(`${h.toFixed(1)}km`))
  for (const [hourIdx, h] of (profile.value?.hours ?? []).entries()) {
    for (const level of h.levels) {
      const km = Math.max(0, Math.round(level.height_m_asl / 100) / 10)
      const y = heights.indexOf(km)
      if (y >= 0) heatData.push([hourIdx, y, level.cloud_cover_pct ?? 0])
    }
  }

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#0f172a',
      borderColor: '#334155',
      textStyle: { color: '#e2e8f0', fontSize: 11 },
      formatter(params: any) {
        const idx = params?.[0]?.dataIndex ?? 0
        const h = hours[idx]
        const wind = h?.weather
          ? `<br/>风 ${windArrow(h.weather.wind_direction)} ${h.weather.wind_speed}m/s` +
            (h.weather.wind_gusts != null ? ` · 阵风 ${h.weather.wind_gusts}m/s` : '')
          : ''
        return `${x[idx] || ''}${wind}<br/>` + params.map((p: any) => `${p.marker}${p.seriesName}: ${Array.isArray(p.value) ? p.value[2] : p.value}`).join('<br/>')
      },
    },
    grid: [
      { left: 36, right: 12, top: 16, height: 48 },
      { left: 36, right: 12, top: 84, height: 54 },
      { left: 36, right: 12, top: 158, height: 92 },
      { left: 36, right: 12, top: 274, height: 48 },
    ],
    xAxis: [0, 1, 2, 3].map((idx) => ({
      type: 'category',
      data: x,
      gridIndex: idx,
      axisLabel: { color: '#94a3b8', fontSize: 9, show: idx === 3 },
      axisLine: { lineStyle: { color: '#334155' } },
      axisTick: { show: false },
    })),
    yAxis: [
      { type: 'value', gridIndex: 0, axisLabel: { color: '#94a3b8', fontSize: 9 }, splitLine: { lineStyle: { color: '#1e293b' } } },
      { type: 'value', gridIndex: 1, axisLabel: { color: '#94a3b8', fontSize: 9 }, splitLine: { lineStyle: { color: '#1e293b' } } },
      { type: 'category', gridIndex: 2, data: heightLabels, axisLabel: { color: '#94a3b8', fontSize: 9 }, splitLine: { show: false } },
      { type: 'value', gridIndex: 3, axisLabel: { color: '#94a3b8', fontSize: 9 }, splitLine: { lineStyle: { color: '#1e293b' } } },
    ],
    visualMap: {
      min: 0,
      max: 100,
      show: false,
      inRange: { color: ['#0f172a', '#334155', '#64748b', '#e2e8f0'] },
    },
    series: [
      { name: '气温 °C', type: 'line', xAxisIndex: 0, yAxisIndex: 0, smooth: true, data: hours.map((h) => h.weather.temperature), lineStyle: { color: '#fb923c' }, symbolSize: 4 },
      { name: '降水 mm', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: hours.map((h) => h.weather.precipitation), itemStyle: { color: '#38bdf8' } },
      { name: '湿度 %', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: hours.map((h) => h.weather.humidity), lineStyle: { color: '#a78bfa' }, symbolSize: 3 },
      { name: '垂直云量 %', type: 'heatmap', xAxisIndex: 2, yAxisIndex: 2, data: heatData, emphasis: { itemStyle: { borderColor: '#7dd3fc', borderWidth: 1 } } },
      { name: '风速 m/s', type: 'line', xAxisIndex: 3, yAxisIndex: 3, data: hours.map((h) => h.weather.wind_speed), lineStyle: { color: '#86efac' }, symbolSize: 4 },
      { name: '阵风 m/s', type: 'line', xAxisIndex: 3, yAxisIndex: 3, data: hours.map((h) => h.weather.wind_gusts ?? null), lineStyle: { color: '#60a5fa', type: 'dashed' }, symbolSize: 3 },
    ],
  })
}

watch(expanded, () => {
  if (expanded.value) void loadProfile()
})

watch(
  () => [day.value?.date, loc.value?.lat, loc.value?.lng],
  () => {
    profile.value = null
    if (expanded.value) void loadProfile()
  },
)

onBeforeUnmount(() => {
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div v-if="store.prediction && store.selectedDay" class="shrink-0 rounded-lg border border-slate-700 bg-slate-900/45 text-[10px] text-slate-300">
    <button
      type="button"
      class="flex w-full items-center justify-between px-2 py-1.5 text-left text-sky-100"
      @click="expanded = !expanded"
    >
      <span>{{ expanded ? '▼' : '▶' }} 气象详图 · Meteogram</span>
      <span class="text-slate-500">模式估算剖面</span>
    </button>
    <div v-show="expanded" class="border-t border-slate-800 p-2">
      <div v-if="loading" class="py-4 text-center text-slate-500">加载剖面数据...</div>
      <div v-else-if="error" class="text-rose-300">{{ error }}</div>
      <div ref="chartRef" class="h-[330px] w-full" />
      <div class="mt-1 grid grid-cols-6 gap-x-1 gap-y-0.5 text-center text-[9px] text-slate-500">
        <span class="col-span-6 text-left text-slate-400">风向（箭头为风吹向）/ 风速</span>
        <span v-for="h in store.dayHours.filter((_, i) => i % 2 === 0).slice(0, 12)" :key="h.time">
          {{ windArrow(h.weather.wind_direction) }} {{ h.weather.wind_speed }}m/s
        </span>
      </div>
    </div>
  </div>
</template>
