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
      :class="[
        compact ? 'p-2' : 'rounded-xl p-3',
        String(key).startsWith('obs_') ? 'border border-slate-800/80' : '',
        String(key).startsWith('ml_') || key === 'fuzzy_reference' ? 'border border-sky-900/40' : '',
      ]"
    >
      <div class="mb-1 flex items-center justify-between" :class="compact ? 'text-xs' : 'text-sm'">
        <span>
          <span
            v-if="String(key).startsWith('obs_')"
            class="mr-1 rounded bg-slate-800 px-1 py-0.5 text-[10px] text-slate-400"
          >观测</span>
          <span
            v-else-if="String(key).startsWith('ml_') || key === 'fuzzy_reference'"
            class="mr-1 rounded bg-sky-950 px-1 py-0.5 text-[10px] text-sky-400"
          >模型</span>
          {{ factor.label }}
        </span>
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
