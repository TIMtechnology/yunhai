<script setup lang="ts">
import type { FactorDetail } from '../services/api'

withDefaults(
  defineProps<{
    title: string
    factors: Record<string, FactorDetail>
    accent: string
    compact?: boolean
  }>(),
  { compact: false },
)
</script>

<template>
  <div :class="compact ? 'space-y-2 p-1' : 'glass space-y-3 p-4'">
    <h3 v-if="!compact" class="mb-3 text-sm font-medium" :style="{ color: accent }">
      {{ title }} · 因子拆解
    </h3>
    <div
      v-for="(factor, key) in factors"
      :key="key"
      class="rounded-lg bg-slate-900/40"
      :class="compact ? 'p-2' : 'rounded-xl p-3'"
    >
      <div class="mb-1 flex items-center justify-between" :class="compact ? 'text-xs' : 'text-sm'">
        <span>{{ factor.label }}</span>
        <span class="text-slate-400">{{ factor.value }}</span>
      </div>
      <div class="mb-1 h-1 overflow-hidden rounded-full bg-slate-800">
        <div
          class="h-full rounded-full transition-all duration-500"
          :style="{ width: `${factor.score * 100}%`, background: accent }"
        />
      </div>
      <p v-if="!compact" class="text-xs leading-relaxed text-slate-500">{{ factor.description }}</p>
      <p v-if="!compact && factor.reference" class="mt-1 text-[11px] leading-relaxed text-slate-600">
        文献：
        <a
          :href="`https://doi.org/${factor.reference}`"
          target="_blank"
          rel="noopener noreferrer"
          class="text-sky-500 hover:underline"
        >{{ factor.reference }}</a>
      </p>
    </div>
  </div>
</template>
