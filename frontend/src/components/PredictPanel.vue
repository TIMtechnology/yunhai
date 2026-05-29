<script setup lang="ts">
import { computed } from 'vue'
import { useAppStore } from '../stores/app'
import { buildLabelPageUrl } from '../services/contributeLabel'
import CloudDebugPanel from './CloudDebugPanel.vue'
import FactorList from './FactorList.vue'
import FactorRadar from './FactorRadar.vue'
import ScenarioHero from './ScenarioHero.vue'

const store = useAppStore()
const hour = computed(() => store.currentHour())

const viewingModeLabel: Record<string, string> = {
  valley_fill: '山谷填云',
  peak_overlook: '峰顶俯瞰',
  ridge_layer: '山脊层云',
  plateau_edge: '台地边缘',
}

function openAnnotate() {
  const loc = store.prediction?.location
  if (!loc) return
  if (loc.spot_id && store.selectedViewpoint) {
    window.open(buildLabelPageUrl({ spot: loc.spot_id, vp: store.selectedViewpoint.id }), '_blank')
    return
  }
  window.open(
    buildLabelPageUrl({
      lat: loc.lat,
      lng: loc.lng,
      name: loc.name,
      elevation: loc.elevation,
    }),
    '_blank',
  )
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-2 overflow-hidden border-l border-slate-800 p-3">
    <template v-if="hour && store.prediction">
      <div
        v-if="store.prediction.location.viewing_mode"
        class="rounded-lg border border-sky-800/50 bg-sky-950/20 px-2 py-1.5 text-[10px] leading-snug text-sky-200"
      >
        观云模式 ·
        {{ viewingModeLabel[store.prediction.location.viewing_mode] || store.prediction.location.viewing_mode }}
        <span v-if="store.prediction.location.viewing_mode_note" class="block text-sky-400/90">
          {{ store.prediction.location.viewing_mode_note }}
        </span>
        <span v-if="store.prediction.location.terrain" class="mt-0.5 block text-sky-400">
          1km峰 {{ store.prediction.location.terrain.elev_max_1km_m }}m · 5km峰
          {{ store.prediction.location.terrain.elev_max_5km_m }}m
          <template v-if="store.prediction.location.terrain.sunrise_azimuth_deg != null">
            · 日出方位 {{ store.prediction.location.terrain.sunrise_azimuth_deg }}°
          </template>
        </span>
      </div>
      <div
        v-if="
          store.prediction.location.viewing_mode === 'peak_overlook' &&
          store.prediction.location.observable
        "
        class="rounded-lg border border-violet-800/50 bg-violet-950/25 px-2 py-1.5 text-[10px] leading-snug text-violet-100"
      >
        可观测云海 · 日出扇区约
        {{ Math.round((store.prediction.location.observable.observable_fraction || 0) * 100) }}%
        <span class="text-violet-300">
          （可见 {{ store.prediction.location.observable.visible_range_km }} km · 可填云
          {{ store.prediction.location.observable.fillable_points }}/{{
            store.prediction.location.observable.eligible_points
          }}
          点）
        </span>
        <span v-if="store.prediction.location.observable.note" class="mt-0.5 block text-violet-300/90">
          {{ store.prediction.location.observable.note }}
        </span>
      </div>
      <div
        v-if="store.prediction.location.ml_status && !store.prediction.location.ml_status.ml_active"
        class="rounded-lg border border-amber-700/40 bg-amber-950/25 px-2 py-1.5 text-[10px] leading-snug text-amber-200"
      >
        {{ store.prediction.location.ml_status.message }}
      </div>
      <div class="flex items-center justify-end gap-2">
        <n-button size="tiny" secondary @click="openAnnotate">标注此点位</n-button>
      </div>
      <ScenarioHero :hour="hour" />

      <CloudDebugPanel v-if="store.showCloudDebug" />

      <div class="glass flex min-h-0 flex-1 flex-col overflow-hidden p-2">
        <n-tabs type="segment" animated size="small" class="tabs-fill">
          <n-tab-pane name="scenario" tab="场景因子" display-directive="show:lazy">
            <FactorRadar
              :cloudsea-factors="hour.cloudsea.factors"
              :sunrise-factors="hour.sunrise.factors"
              mode="cloudsea"
              compact
            />
          </n-tab-pane>
          <n-tab-pane name="cloudsea" tab="云海" display-directive="show:lazy">
            <FactorList title="云海" :factors="hour.cloudsea.factors" accent="#38bdf8" compact />
          </n-tab-pane>
          <n-tab-pane name="sunrise" tab="日出" display-directive="show:lazy">
            <FactorList title="日出" :factors="hour.sunrise.factors" accent="#fb923c" compact />
          </n-tab-pane>
        </n-tabs>
      </div>
    </template>

    <div v-else class="glass flex flex-1 items-center justify-center p-6 text-center text-sm text-slate-500">
      选择观景点后显示联合场景预测
    </div>
  </div>
</template>

<style scoped>
.tabs-fill {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
}

.tabs-fill :deep(.n-tabs-nav) {
  flex-shrink: 0;
}

.tabs-fill :deep(.n-tabs-pane-wrapper) {
  flex: 1;
  min-height: 0;
}

.tabs-fill :deep(.n-tab-pane) {
  height: 100%;
  overflow: auto;
}
</style>
