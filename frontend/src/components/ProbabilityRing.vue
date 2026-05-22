<script setup lang="ts">
import { computed } from 'vue'
import { GRADE_COLORS } from '../config'

const props = defineProps<{
  value: number
  label: string
  color: string
  grade?: string
}>()

const radius = 54
const circumference = 2 * Math.PI * radius
const dash = computed(() => `${(props.value / 100) * circumference} ${circumference}`)
const gradeColor = computed(() => GRADE_COLORS[props.grade || ''] || '#94a3b8')
</script>

<template>
  <div class="flex flex-col items-center gap-2">
    <div class="relative h-32 w-32">
      <svg class="h-full w-full -rotate-90" viewBox="0 0 120 120">
        <circle cx="60" cy="60" :r="radius" fill="none" stroke="#1e293b" stroke-width="10" />
        <circle
          cx="60"
          cy="60"
          :r="radius"
          fill="none"
          :stroke="color"
          stroke-width="10"
          stroke-linecap="round"
          :stroke-dasharray="dash"
          class="transition-all duration-700"
        />
      </svg>
      <div class="absolute inset-0 flex flex-col items-center justify-center">
        <span class="text-3xl font-semibold tabular-nums">{{ value }}%</span>
        <span v-if="grade" class="text-xs" :style="{ color: gradeColor }">{{ grade }}</span>
      </div>
    </div>
    <span class="text-sm text-slate-400">{{ label }}</span>
  </div>
</template>
