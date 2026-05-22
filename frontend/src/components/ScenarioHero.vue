<script setup lang="ts">
import { computed } from 'vue'
import type { HourPrediction } from '../services/api'
import { GRADE_COLORS } from '../config'

const props = defineProps<{ hour: HourPrediction }>()

const levelMeta: Record<number, { color: string; badge: string }> = {
  1: { color: '#34D399', badge: '推荐' },
  2: { color: '#38BDF8', badge: '可关注' },
  3: { color: '#F87171', badge: '不推荐' },
}

const meta = computed(() => levelMeta[props.hour.scenario.level] ?? levelMeta[2])
const w = computed(() => props.hour.weather)
</script>

<template>
  <div class="glass shrink-0 overflow-hidden">
    <div
      class="px-3 py-2"
      :style="{
        background: `linear-gradient(135deg, ${meta.color}22, transparent 60%)`,
        borderBottom: '1px solid #1e293b',
      }"
    >
      <div class="flex items-start justify-between gap-2">
        <div class="min-w-0 flex-1">
          <div class="mb-0.5 flex items-center gap-2">
            <span
              class="rounded-full px-2 py-0.5 text-[10px] font-medium"
              :style="{ background: `${meta.color}33`, color: meta.color }"
            >
              {{ meta.badge }}
            </span>
            <span v-if="hour.is_sunrise_window" class="text-[10px] text-orange-300">日出时段</span>
          </div>
          <h2 class="truncate text-base font-bold">{{ hour.scenario.label }}</h2>
          <p class="mt-0.5 line-clamp-2 text-[11px] leading-snug text-slate-400">
            {{ hour.scenario.narrative }}
          </p>
        </div>
        <div class="grid shrink-0 grid-cols-3 gap-1 text-center">
          <div class="rounded-lg bg-slate-900/60 px-2 py-1">
            <div class="text-[9px] text-slate-500">综合</div>
            <div class="text-sm font-semibold tabular-nums" :style="{ color: meta.color }">
              {{ hour.scenario.combined_score }}
            </div>
          </div>
          <div class="rounded-lg bg-slate-900/60 px-2 py-1">
            <div class="text-[9px] text-slate-500">云海</div>
            <div class="text-sm font-semibold tabular-nums text-sky-300">{{ hour.cloudsea.probability }}%</div>
            <div class="text-[9px]" :style="{ color: GRADE_COLORS[hour.cloudsea.grade] }">
              {{ hour.cloudsea.grade }}
            </div>
          </div>
          <div class="rounded-lg bg-slate-900/60 px-2 py-1">
            <div class="text-[9px] text-slate-500">日出</div>
            <div class="text-sm font-semibold tabular-nums text-orange-300">{{ hour.sunrise.probability }}%</div>
            <div class="text-[9px]" :style="{ color: GRADE_COLORS[hour.sunrise.grade] }">
              {{ hour.sunrise.grade }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="flex flex-wrap gap-1.5 px-3 py-2 text-[10px] text-slate-400">
      <span class="rounded-md bg-slate-800/80 px-1.5 py-0.5">{{ w.weather_text }}</span>
      <span class="rounded-md bg-slate-800/80 px-1.5 py-0.5">{{ w.temperature }}°C</span>
      <span class="rounded-md bg-slate-800/80 px-1.5 py-0.5">湿度 {{ w.humidity }}%</span>
      <span class="rounded-md bg-slate-800/80 px-1.5 py-0.5">
        云 {{ w.cloud_cover_low }}/{{ w.cloud_cover_mid }}/{{ w.cloud_cover_high }}%
      </span>
      <span v-if="hour.sunrise.sun_time" class="rounded-md bg-orange-500/10 px-1.5 py-0.5 text-orange-300">
        日出 {{ hour.sunrise.sun_time }}
      </span>
    </div>
  </div>
</template>
