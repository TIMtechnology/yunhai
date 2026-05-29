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
