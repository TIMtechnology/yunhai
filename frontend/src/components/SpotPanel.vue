<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useAppStore } from '../stores/app'
import type { SpotSearchResult } from '../services/api'

const store = useAppStore()
const localQuery = ref('')

let searchTimer: ReturnType<typeof setTimeout> | null = null

watch(localQuery, (q) => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    if (q.trim().length >= 1) {
      store.doSearch(q)
    } else {
      store.doSearch('')
    }
  }, 300)
})

const presets = [
  { id: 'wunvshan', label: '五女山' },
  { id: 'donglingshan', label: '东灵山' },
  { id: 'daheishan', label: '大黑山' },
  { id: 'huangshan', label: '黄山' },
  { id: 'lushan', label: '庐山' },
]

function sourceLabel(item: SpotSearchResult) {
  return item.source === 'curated' ? '精选' : '天地图'
}

function sourceClass(item: SpotSearchResult) {
  return item.source === 'curated'
    ? 'bg-violet-500/15 text-violet-300'
    : 'bg-emerald-500/15 text-emerald-300'
}

const hasSearchResults = computed(
  () => store.curatedResults.length > 0 || store.poiResults.length > 0,
)
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-2 overflow-hidden border-r border-slate-800 p-3">
    <div class="glass shrink-0 p-3">
      <h2 class="mb-2 text-sm font-semibold">地点搜索</h2>
      <n-input
        v-model:value="localQuery"
        placeholder="搜索景区或 POI，如：云台山、大黑山"
        clearable
        size="small"
      />
      <div class="mt-1.5 text-[10px] text-slate-500">
        支持天地图 POI 与精选观景点；POI 结果带绿色「天地图」标签
      </div>
      <div class="mt-2 flex flex-wrap gap-1.5">
        <n-button
          v-for="p in presets"
          :key="p.id"
          size="tiny"
          tertiary
          type="info"
          @click="store.selectSpot(p.id)"
        >
          {{ p.label }}
        </n-button>
      </div>
    </div>

    <div
      v-if="store.poiSearchError && localQuery.trim()"
      class="glass shrink-0 p-2 text-[10px] text-amber-400"
    >
      {{ store.poiSearchError }}
    </div>

    <div
      v-else-if="localQuery.trim() && !hasSearchResults && !store.loading"
      class="glass shrink-0 p-2 text-[10px] text-slate-500"
    >
      未找到匹配地点，可尝试加地区名，如「焦作云台山」
    </div>

    <div v-if="hasSearchResults" class="glass max-h-40 shrink-0 overflow-auto p-2">
      <div v-if="store.curatedResults.length" class="mb-2">
        <div class="mb-1 px-1 text-[10px] font-medium text-violet-300">精选景区</div>
        <button
          v-for="item in store.curatedResults"
          :key="item.id"
          class="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-slate-800/70"
          @click="store.selectPoiResult(item)"
        >
          <span class="mt-0.5 shrink-0 rounded px-1 py-0.5 text-[9px]" :class="sourceClass(item)">
            {{ sourceLabel(item) }}
          </span>
          <div class="min-w-0">
            <div class="truncate text-xs">{{ item.name }}</div>
            <div class="truncate text-[10px] text-slate-500">
              {{ item.region || '未知' }}
              <span v-if="item.viewpoint_count"> · {{ item.viewpoint_count }} 个观景点</span>
            </div>
          </div>
        </button>
      </div>

      <div v-if="store.poiResults.length">
        <div class="mb-1 px-1 text-[10px] font-medium text-emerald-300">天地图 POI</div>
        <button
          v-for="item in store.poiResults"
          :key="item.id"
          class="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-slate-800/70"
          @click="store.selectPoiResult(item)"
        >
          <span class="mt-0.5 shrink-0 rounded px-1 py-0.5 text-[9px]" :class="sourceClass(item)">
            {{ sourceLabel(item) }}
          </span>
          <div class="min-w-0">
            <div class="truncate text-xs">{{ item.name }}</div>
            <div class="truncate text-[10px] text-slate-500">
              {{ item.address || item.region || '未知地址' }}
            </div>
            <div v-if="item.lat != null && item.lng != null" class="text-[10px] text-slate-600">
              {{ item.lat.toFixed(4) }}, {{ item.lng.toFixed(4) }}
            </div>
          </div>
        </button>
      </div>
    </div>

    <div v-if="store.currentSpot" class="glass flex min-h-0 flex-1 flex-col overflow-hidden p-3">
      <div class="shrink-0">
        <div class="truncate text-sm font-semibold">{{ store.currentSpot.name }}</div>
        <div class="text-[10px] text-slate-400">
          {{ store.currentSpot.region }} · {{ store.currentSpot.peak_elevation }}m
        </div>
      </div>

      <div class="mt-2 min-h-0 flex-1 space-y-1.5 overflow-auto">
        <button
          v-for="vp in store.currentSpot.viewpoints"
          :key="vp.id"
          class="w-full rounded-lg border px-2.5 py-2 text-left transition"
          :class="
            store.selectedViewpoint?.id === vp.id
              ? 'border-sky-400/60 bg-sky-400/10'
              : 'border-slate-700 hover:border-slate-500'
          "
          @click="store.selectViewpoint(vp)"
        >
          <div class="text-xs font-medium">{{ vp.name }}</div>
          <div class="mt-0.5 text-[10px] text-slate-400">
            {{ vp.elevation }}m · {{ vp.lat.toFixed(4) }}, {{ vp.lng.toFixed(4) }}
          </div>
          <div v-if="vp.note" class="mt-0.5 text-[10px] text-slate-500">{{ vp.note }}</div>
        </button>
      </div>

      <div v-if="store.poiSuggestions.length" class="mt-2 shrink-0 border-t border-slate-700/80 pt-2">
        <div class="mb-1 text-[10px] font-medium text-emerald-300">天地图定位建议</div>
        <div class="max-h-24 space-y-1 overflow-auto">
          <button
            v-for="item in store.poiSuggestions"
            :key="item.id"
            class="flex w-full items-start gap-1.5 rounded-lg px-1.5 py-1 text-left hover:bg-slate-800/70"
            @click="store.applyPoiLocation(item)"
          >
            <span class="mt-0.5 shrink-0 rounded bg-emerald-500/15 px-1 py-0.5 text-[9px] text-emerald-300">
              POI
            </span>
            <div class="min-w-0">
              <div class="truncate text-[11px]">{{ item.name }}</div>
              <div class="truncate text-[10px] text-slate-500">
                {{ item.address || item.region }}
              </div>
            </div>
          </button>
        </div>
      </div>
    </div>

    <div v-else-if="store.prediction" class="glass shrink-0 space-y-2 p-3">
      <div>
        <div class="text-[10px] text-slate-400">当前位置（天地图 POI / 自定义）</div>
        <div class="truncate text-sm font-semibold">{{ store.prediction.location.name }}</div>
        <div class="mt-0.5 text-[10px] text-slate-500">
          {{ store.prediction.location.lat.toFixed(5) }},
          {{ store.prediction.location.lng.toFixed(5) }} ·
          {{ Math.round(store.prediction.location.elevation) }}m
        </div>
      </div>
      <div class="text-[10px] text-slate-500">
        可在右侧地图拖动标记或开启「点击放置」微调位置
      </div>
    </div>

    <div
      v-else
      class="glass flex flex-1 items-center justify-center p-4 text-center text-xs text-slate-500"
    >
      搜索天地图 POI 或点选精选景区
    </div>
  </div>
</template>
