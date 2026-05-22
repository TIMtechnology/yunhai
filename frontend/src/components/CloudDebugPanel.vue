<script setup lang="ts">
import { computed } from 'vue'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const debug = computed(() => store.cloudDebug)

const modeLabel = computed(() => {
  switch (debug.value.mode) {
    case 'loading':
      return '加载中'
    case 'satellite':
      return '卫星真值'
    case 'fallback':
      return '示意回退'
    case 'off':
      return '已关闭'
    default:
      return debug.value.mode
  }
})

const boundsText = computed(() => {
  const b = debug.value.bounds
  if (!b) return '—'
  return `${b.west.toFixed(4)}, ${b.south.toFixed(4)} → ${b.east.toFixed(4)}, ${b.north.toFixed(4)}`
})

const overlayText = computed(() => {
  const r = debug.value.overlayRect
  if (!r) return '—'
  return `left ${Math.round(r.left)} · top ${Math.round(r.top)} · ${Math.round(r.width)}×${Math.round(r.height)}`
})
</script>

<template>
  <div class="glass shrink-0 overflow-hidden p-3">
    <div class="mb-2 flex items-center justify-between gap-2">
      <div class="text-xs font-medium text-sky-300">卫星云图调试</div>
      <span
        class="rounded px-1.5 py-0.5 text-[10px]"
        :class="
          debug.mode === 'satellite'
            ? 'bg-sky-500/15 text-sky-300'
            : debug.mode === 'fallback'
              ? 'bg-amber-500/15 text-amber-300'
              : 'bg-slate-500/15 text-slate-400'
        "
      >
        {{ modeLabel }}
      </span>
    </div>

    <div
      class="relative mb-2 flex aspect-square w-full items-center justify-center overflow-hidden rounded-xl border border-slate-700 bg-slate-950"
    >
      <img
        v-if="debug.imageUrl"
        :src="debug.imageUrl"
        alt="卫星云图原始裁切"
        class="max-h-full max-w-full object-contain"
      />
      <div v-else-if="debug.mode === 'loading'" class="text-xs text-slate-500">加载云图中…</div>
      <div v-else class="text-xs text-slate-500">暂无云图数据</div>
    </div>

    <dl class="space-y-1 text-[10px] text-slate-400">
      <div class="flex justify-between gap-2">
        <dt>UTC 时次</dt>
        <dd class="text-right text-slate-300">{{ debug.datetimeUtc || '—' }}</dd>
      </div>
      <div class="flex justify-between gap-2">
        <dt>图像大小</dt>
        <dd class="text-right text-slate-300">
          {{ debug.imageBytes ? `${(debug.imageBytes / 1024).toFixed(1)} KB` : '—' }}
        </dd>
      </div>
      <div class="flex justify-between gap-2">
        <dt>裁切范围</dt>
        <dd class="max-w-[180px] truncate text-right text-slate-300" :title="boundsText">{{ boundsText }}</dd>
      </div>
      <div class="flex justify-between gap-2">
        <dt>地图贴图位置</dt>
        <dd class="max-w-[180px] truncate text-right text-slate-300" :title="overlayText">{{ overlayText }}</dd>
      </div>
      <div v-if="debug.lookbackHours > 0" class="flex justify-between gap-2">
        <dt>时间回溯</dt>
        <dd class="text-right text-amber-300">{{ debug.lookbackHours }}h</dd>
      </div>
      <div v-if="debug.reason" class="flex justify-between gap-2">
        <dt>备注</dt>
        <dd class="text-right text-slate-300">{{ debug.reason }}</dd>
      </div>
      <div v-if="debug.error" class="rounded bg-red-500/10 px-2 py-1 text-red-300">{{ debug.error }}</div>
    </dl>

    <div class="mt-2 text-[10px] leading-relaxed text-slate-500">
      数据源：NASA GIBS · Himawari Band13 红外。此处为 API 原始裁切图，应与地图叠加一致。
    </div>
  </div>
</template>
