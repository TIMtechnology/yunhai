<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { GITHUB_REPO_URL } from './config'
import { trackPageVisit } from './services/analytics'
import { useAppStore } from './stores/app'
import ForecastTimeline from './components/ForecastTimeline.vue'
import MapPanel from './components/MapPanel.vue'
import PredictPanel from './components/PredictPanel.vue'
import SpotPanel from './components/SpotPanel.vue'

const store = useAppStore()

const mapTarget = computed(() => {
  const coords = store.mapCoords
  if (store.selectedViewpoint && coords) {
    const adjusted = store.markerAdjusted ? '（已调整）' : ''
    return {
      lng: coords.lng,
      lat: coords.lat,
      label: `${store.currentSpot?.name || ''} · ${store.selectedViewpoint.name}${adjusted}`,
      zoom: 14,
    }
  }
  if (store.prediction && coords) {
    return {
      lng: coords.lng,
      lat: coords.lat,
      label: store.prediction.location.name,
      zoom: 12,
    }
  }
  return { lng: 125.408, lat: 41.32, label: '本溪五女山（默认）', zoom: 12 }
})

const currentHour = computed(() => store.currentHour())

onMounted(() => {
  trackPageVisit()
  store.selectSpot('wunvshan')
})

function openGuide() {
  window.open('/docs/index.html', '_blank')
}

function openGithub() {
  window.open(GITHUB_REPO_URL, '_blank', 'noopener,noreferrer')
}
</script>

<template>
  <n-config-provider :theme="null">
    <n-message-provider>
      <div class="flex h-screen w-screen flex-col overflow-hidden">
        <header class="flex h-14 shrink-0 items-center justify-between border-b border-slate-800 px-5">
          <div class="flex items-center gap-3">
            <div
              class="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-sky-400 to-orange-400 text-lg"
            >
              ☁
            </div>
            <div>
              <div class="text-sm font-semibold">日出云海 · 联合观测预测</div>
              <div class="text-[11px] text-slate-500">未来 5 天 · 场景判断 · 卫星云图裁切</div>
            </div>
          </div>
          <div class="flex items-center gap-3">
            <div v-if="store.prediction" class="flex items-center gap-3 text-xs text-slate-400">
              <div class="flex items-center gap-2">
                <span>云层蒙版</span>
                <n-switch v-model:value="store.showCloudOverlay" size="small" />
              </div>
              <div class="flex items-center gap-2">
                <span>云图调试</span>
                <n-switch v-model:value="store.showCloudDebug" size="small" />
              </div>
            </div>
            <n-spin v-if="store.loading" size="small" />
            <n-button quaternary size="small" @click="openGithub">
              <span class="inline-flex items-center gap-1.5">
                <svg class="h-3.5 w-3.5" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                  <path
                    d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"
                  />
                </svg>
                GitHub
              </span>
            </n-button>
            <n-button type="primary" secondary size="small" @click="openGuide">📖 使用指南</n-button>
          </div>
        </header>

        <n-alert
          v-if="store.error"
          type="error"
          :title="store.error"
          class="mx-4 mt-2 shrink-0"
          closable
        />

        <div class="grid min-h-0 flex-1 grid-cols-[260px_1fr_380px] overflow-hidden">
          <SpotPanel />
          <div class="grid min-h-0 grid-rows-[minmax(0,1fr)_auto] gap-2 overflow-hidden p-3">
            <MapPanel
              :key="store.currentSpot?.id ?? store.prediction?.location.name ?? 'default'"
              class="min-h-0"
              :lng="mapTarget.lng"
              :lat="mapTarget.lat"
              :label="mapTarget.label"
              :zoom="mapTarget.zoom"
              :weather="currentHour?.weather"
              :weather-time="currentHour?.time"
              :spot-id="store.currentSpot?.id"
              :cloud-base="currentHour?.cloudsea.cloud_base_m"
              :elevation="store.prediction?.location.elevation"
              :show-overlay="store.showCloudOverlay"
            />
            <ForecastTimeline v-if="store.prediction" class="shrink-0" />
          </div>
          <PredictPanel />
        </div>
      </div>
    </n-message-provider>
  </n-config-provider>
</template>

<style scoped>
:deep(.n-config-provider) {
  height: 100%;
}
</style>
