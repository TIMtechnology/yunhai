<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { FactorDetail } from '../services/api'
import { CLOUDSEA_COLOR, SUNRISE_COLOR } from '../config'

const props = withDefaults(
  defineProps<{
    cloudseaFactors: Record<string, FactorDetail>
    sunriseFactors: Record<string, FactorDetail>
    mode: 'cloudsea' | 'sunrise'
    compact?: boolean
  }>(),
  { compact: false },
)

const chartRef = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null

const factors = computed(() => {
  const source = props.mode === 'cloudsea' ? props.cloudseaFactors : props.sunriseFactors
  return Object.fromEntries(
    Object.entries(source).filter(
      ([key, f]) => f.weight > 0 && !key.startsWith('ml_') && key !== 'fuzzy_reference',
    ),
  )
})

function render() {
  if (!chartRef.value) return
  if (!chart) chart = echarts.init(chartRef.value, undefined, { renderer: 'canvas' })

  const entries = Object.values(factors.value)
  chart.setOption({
    backgroundColor: 'transparent',
    radar: {
      indicator: entries.map((f) => ({ name: f.label, max: 1 })),
      radius: props.compact ? '58%' : '62%',
      center: ['50%', '52%'],
      splitArea: { areaStyle: { color: ['rgba(30,41,59,0.2)', 'rgba(15,23,42,0.3)'] } },
      axisName: { color: '#94a3b8', fontSize: props.compact ? 9 : 11 },
      splitLine: { lineStyle: { color: '#334155' } },
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            value: entries.map((f) => f.score),
            name: props.mode === 'cloudsea' ? '云海因子' : '日出因子',
            areaStyle: {
              color:
                props.mode === 'cloudsea'
                  ? 'rgba(56,189,248,0.25)'
                  : 'rgba(251,146,60,0.25)',
            },
            lineStyle: {
              color: props.mode === 'cloudsea' ? CLOUDSEA_COLOR : SUNRISE_COLOR,
            },
            itemStyle: {
              color: props.mode === 'cloudsea' ? CLOUDSEA_COLOR : SUNRISE_COLOR,
            },
          },
        ],
      },
    ],
  })
  chart.resize()
}

watch(factors, render, { deep: true })
onMounted(() => {
  render()
  window.addEventListener('resize', () => chart?.resize())
})
onBeforeUnmount(() => chart?.dispose())
</script>

<template>
  <div ref="chartRef" class="h-full min-h-[120px] w-full" />
</template>
