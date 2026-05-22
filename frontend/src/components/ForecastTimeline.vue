<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { useAppStore } from '../stores/app'
import { CLOUDSEA_COLOR, SUNRISE_COLOR } from '../config'

const store = useAppStore()
const chartRef = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null

const hourLabels = computed(() =>
  store.dayHours.map((h) => {
    const d = new Date(h.time)
    return `${String(d.getHours()).padStart(2, '0')}:00`
  }),
)

const selectedLabel = computed(() => hourLabels.value[store.dayLocalIndex] || '--:--')

function formatDayTab(day: (typeof store.days)[0]) {
  return `${day.date.slice(5).replace('-', '/')} ${day.weekday}`
}

function renderChart() {
  if (!chartRef.value || !store.dayHours.length) return
  if (!chart) chart = echarts.init(chartRef.value, undefined, { renderer: 'canvas' })

  const selected = store.dayLocalIndex
  const sunriseIdx = store.dayHours.findIndex((h) => h.is_sunrise_window)
  const markLine =
    sunriseIdx >= 0
      ? {
          symbol: 'none',
          data: [{ xAxis: hourLabels.value[sunriseIdx], name: '日出' }],
          lineStyle: { color: '#fb923c', type: 'dashed', width: 1 },
          label: { formatter: '日出', color: '#fb923c', fontSize: 10 },
        }
      : undefined

  const symbolSize = store.dayHours.map((_, idx) => (idx === selected ? 10 : 5))

  chart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 36, right: 8, top: 20, bottom: 24 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#0f172a',
      borderColor: '#334155',
      textStyle: { color: '#e2e8f0', fontSize: 11 },
      formatter(params: any) {
        const idx = params[0]?.dataIndex ?? 0
        const h = store.dayHours[idx]
        if (!h) return ''
        return [
          `<b>${hourLabels.value[idx]}</b> ${h.scenario.label}`,
          `云海 ${h.cloudsea.probability}% · 日出 ${h.sunrise.probability}%`,
          `<span style="color:#64748b">点击切换到该时刻</span>`,
        ].join('<br/>')
      },
    },
    xAxis: {
      type: 'category',
      data: hourLabels.value,
      axisLabel: { color: '#64748b', fontSize: 10, interval: Math.max(0, Math.floor(hourLabels.value.length / 8)) },
      axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      splitNumber: 2,
      splitLine: { lineStyle: { color: '#1e293b' } },
      axisLabel: { color: '#64748b', fontSize: 10 },
    },
    series: [
      {
        name: '云海',
        type: 'line',
        smooth: true,
        symbol: 'circle',
        symbolSize,
        showSymbol: true,
        data: store.dayHours.map((h) => h.cloudsea.probability),
        lineStyle: { color: CLOUDSEA_COLOR, width: 2 },
        itemStyle: {
          color: (params: any) => (params.dataIndex === selected ? '#7dd3fc' : CLOUDSEA_COLOR),
          borderColor: (params: any) => (params.dataIndex === selected ? '#fff' : CLOUDSEA_COLOR),
          borderWidth: (params: any) => (params.dataIndex === selected ? 2 : 0),
        },
        areaStyle: { color: 'rgba(56,189,248,0.1)' },
        markLine,
      },
      {
        name: '日出',
        type: 'line',
        smooth: true,
        symbol: 'circle',
        symbolSize: store.dayHours.map((_, idx) => (idx === selected ? 8 : 4)),
        showSymbol: true,
        data: store.dayHours.map((h) => h.sunrise.probability),
        lineStyle: { color: SUNRISE_COLOR, width: 2 },
        itemStyle: {
          color: (params: any) => (params.dataIndex === selected ? '#fdba74' : SUNRISE_COLOR),
        },
        areaStyle: { color: 'rgba(251,146,60,0.08)' },
      },
    ],
  })

  chart.off('click')
  chart.on('click', (params: any) => {
    if (params.dataIndex != null) store.selectHourByDayLocal(params.dataIndex)
  })
  chart.getZr().off('click')
  chart.getZr().on('click', (event: any) => {
    if (!chart) return
    const point: [number, number] = [event.offsetX, event.offsetY]
    if (!chart.containPixel('grid', point)) return
    const xIndex = chart.convertFromPixel({ seriesIndex: 0 }, point)[0]
    const idx = Math.round(Number(xIndex))
    if (idx >= 0 && idx < store.dayHours.length) store.selectHourByDayLocal(idx)
  })
  chart.resize()
}

function onResize() {
  chart?.resize()
}

watch(
  () => [store.dayHours, store.selectedDayIndex, store.dayLocalIndex],
  () => {
    renderChart()
  },
  { deep: true },
)

onMounted(() => {
  renderChart()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
})
</script>

<template>
  <div v-if="store.prediction && store.days.length" class="glass flex max-h-[190px] flex-col gap-2 overflow-hidden p-3">
    <div class="flex shrink-0 items-center justify-between gap-2">
      <div class="flex min-w-0 flex-1 gap-1.5 overflow-x-auto">
        <button
          v-for="(day, idx) in store.days"
          :key="day.date"
          class="shrink-0 rounded-lg border px-2.5 py-1.5 text-left transition"
          :class="
            store.selectedDayIndex === idx
              ? 'border-sky-400/60 bg-sky-400/10'
              : 'border-slate-700 hover:border-slate-500'
          "
          @click="store.selectDay(idx)"
        >
          <div class="text-xs font-medium whitespace-nowrap">{{ formatDayTab(day) }}</div>
          <div class="text-[10px] text-slate-500 whitespace-nowrap">
            日出 {{ day.sunrise_time || '--' }} · {{ day.sunrise_scenario_label || '—' }}
          </div>
        </button>
      </div>
      <div class="flex shrink-0 gap-1">
        <n-button size="tiny" tertiary type="warning" @click="store.jumpToSunrise()">日出</n-button>
        <n-button size="tiny" tertiary type="info" @click="store.jumpToPeakCloudsea()">云海</n-button>
      </div>
    </div>

    <div class="flex shrink-0 items-center justify-between px-1 text-[10px] text-slate-400">
      <span>{{ selectedLabel }}</span>
      <span v-if="store.currentHour()">{{ store.currentHour()!.scenario.label }}</span>
      <span class="text-slate-500">点击折线/整点切换时刻</span>
    </div>

    <div ref="chartRef" class="min-h-[88px] flex-1 w-full cursor-pointer" />
  </div>
</template>
