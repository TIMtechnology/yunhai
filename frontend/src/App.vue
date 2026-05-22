<script setup lang="ts">
import { computed, onMounted } from 'vue'
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
  return { lng: 125.398, lat: 41.261, label: '本溪五女山（默认）', zoom: 11 }
})

const currentHour = computed(() => store.currentHour())

onMounted(() => {
  store.selectSpot('wunvshan')
})

function openGuide() {
  window.open('/docs/index.html', '_blank')
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
