<script setup lang="ts">
import { computed } from 'vue'
import type { HourPrediction } from '../services/api'
import { CLOUDSEA_COLOR, SUNRISE_COLOR } from '../config'

const props = defineProps<{
  hours: HourPrediction[]
  modelValue: number
}>()

const emit = defineEmits<{ 'update:modelValue': [value: number] }>()

const labels = computed(() =>
  props.hours.map((h) => {
    const d = new Date(h.time)
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:00`
  }),
)

function probColor(prob: number, base: string) {
  const alpha = 0.25 + (prob / 100) * 0.75
  return `${base}${Math.round(alpha * 255)
    .toString(16)
    .padStart(2, '0')}`
}
</script>

<template>
  <div class="glass p-4">
    <div class="mb-3 flex items-center justify-between">
      <h3 class="text-sm font-medium text-slate-300">未来 72 小时预测时间轴</h3>
      <div class="flex gap-4 text-xs text-slate-400">
        <span class="flex items-center gap-1"><i class="inline-block h-2 w-2 rounded-full" :style="{ background: CLOUDSEA_COLOR }" />云海</span>
        <span class="flex items-center gap-1"><i class="inline-block h-2 w-2 rounded-full" :style="{ background: SUNRISE_COLOR }" />日出</span>
      </div>
    </div>
    <div class="overflow-x-auto pb-2">
      <div class="flex min-w-max gap-1">
        <button
          v-for="(hour, idx) in hours"
          :key="hour.time"
          class="group flex w-8 flex-col items-center gap-1 rounded-lg p-1 transition"
          :class="modelValue === idx ? 'bg-slate-700/60 ring-1 ring-sky-400/50' : 'hover:bg-slate-800/60'"
          @click="emit('update:modelValue', idx)"
        >
          <div
            class="h-10 w-5 rounded-sm"
            :style="{ background: probColor(hour.cloudsea.probability, '#38bdf8') }"
            :title="`云海 ${hour.cloudsea.probability}%`"
          />
          <div
            class="h-10 w-5 rounded-sm"
            :style="{ background: probColor(hour.sunrise.probability, '#fb923c') }"
            :title="`日出 ${hour.sunrise.probability}%`"
          />
          <span v-if="idx % 6 === 0" class="text-[10px] text-slate-500 whitespace-nowrap">
            {{ labels[idx] }}
          </span>
        </button>
      </div>
    </div>
  </div>
</template>
