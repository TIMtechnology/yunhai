<script setup lang="ts">
import { ref, watch } from 'vue'
import { useAppStore } from '../stores/app'
import { fetchDailyAdvisory, type DailyAdvisoryResponse } from '../services/api'
import { renderAdvisoryMarkdown } from '../utils/advisoryMarkdown'

const store = useAppStore()
const loading = ref(false)
const error = ref('')
const advisory = ref<DailyAdvisoryResponse | null>(null)
const expanded = ref(true)

async function loadBrief(refresh = false) {
  const pred = store.prediction
  const day = store.selectedDay
  if (!pred || !day) {
    advisory.value = null
    return
  }
  loading.value = true
  error.value = ''
  try {
    advisory.value = await fetchDailyAdvisory({
      date: day.date,
      prediction: pred,
      refresh,
    })
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    advisory.value = null
  } finally {
    loading.value = false
  }
}

watch(
  () => [store.prediction, store.selectedDayIndex] as const,
  () => {
    void loadBrief(false)
  },
  { immediate: true },
)
</script>

<template>
  <div
    v-if="store.prediction && store.selectedDay"
    class="shrink-0 rounded-lg border border-indigo-700/60 bg-indigo-950/30 text-[11px] leading-relaxed text-indigo-100"
  >
    <div class="flex items-center justify-between gap-2 border-b border-indigo-800/50 px-2 py-1.5">
      <button
        type="button"
        class="flex min-w-0 flex-1 items-center gap-1 text-left font-medium text-indigo-100"
        @click="expanded = !expanded"
      >
        <span class="shrink-0 text-indigo-300">{{ expanded ? '▼' : '▶' }}</span>
        <span class="truncate">AI 出行解读 · {{ store.selectedDay.date }}</span>
        <span class="hidden font-normal text-indigo-400 sm:inline">（辅助说明）</span>
      </button>
      <n-button
        size="tiny"
        type="primary"
        secondary
        :loading="loading"
        :disabled="loading"
        @click="loadBrief(true)"
      >
        刷新解读
      </n-button>
    </div>

    <div v-show="expanded" class="advisory-scroll max-h-[min(11rem,26vh)] overflow-y-auto px-2.5 py-2">
      <n-spin :show="loading" size="small">
        <div v-if="error" class="text-rose-300">{{ error }}</div>
        <div v-else-if="advisory && !advisory.enabled" class="text-indigo-200/90">
          {{ advisory.message }}
          <div v-if="advisory.context" class="mt-1 text-indigo-400">
            服务端配置 LLM_ADVISORY_ENABLED=true 与 LLM_API_KEY 后可启用。
          </div>
        </div>
        <div v-else-if="advisory?.error" class="text-amber-200">{{ advisory.message || advisory.error }}</div>
        <div v-else-if="advisory?.brief" class="prose-advisory">
          <div v-html="renderAdvisoryMarkdown(advisory.brief)" />
          <div class="mt-2 flex flex-wrap gap-x-2 gap-y-0.5 border-t border-indigo-800/40 pt-1.5 text-[10px] text-indigo-400">
            <span v-if="advisory.model">模型 {{ advisory.model }}</span>
            <span v-if="advisory.cached">服务端缓存（同天气约 24h）</span>
            <span v-else-if="advisory.generated_at">刚生成</span>
            <span
              v-if="
                (advisory.context?.ml_calibration as { loocv_accuracy?: number } | undefined)
                  ?.loocv_accuracy != null
              "
            >
              训练 LOOCV
              {{
                Math.round(
                  ((advisory.context?.ml_calibration as { loocv_accuracy: number }).loocv_accuracy ||
                    0) * 100,
                )
              }}%
            </span>
          </div>
        </div>
        <div v-else-if="!loading" class="text-indigo-400">选择日期后自动生成解读</div>
      </n-spin>
    </div>
  </div>
</template>

<style scoped>
.advisory-scroll {
  scrollbar-width: thin;
  scrollbar-color: rgb(99 102 241 / 0.5) transparent;
}

.prose-advisory :deep(.advisory-h4) {
  margin: 0.5rem 0 0.25rem;
  font-size: 0.75rem;
  font-weight: 600;
  color: rgb(186 230 253);
}

.prose-advisory :deep(.advisory-h4:first-child) {
  margin-top: 0;
}

.prose-advisory :deep(.advisory-h5) {
  margin: 0.35rem 0 0.15rem;
  font-size: 0.7rem;
  font-weight: 500;
  color: rgb(203 213 225);
}

.prose-advisory :deep(.advisory-p) {
  color: rgb(224 231 255 / 0.95);
}

.prose-advisory :deep(.advisory-ul) {
  color: rgb(224 231 255 / 0.92);
}

.prose-advisory :deep(.advisory-table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 10px;
  line-height: 1.35;
}

.prose-advisory :deep(.advisory-th),
.prose-advisory :deep(.advisory-td) {
  border: 1px solid rgb(67 56 202 / 0.45);
  padding: 0.2rem 0.35rem;
  text-align: left;
  white-space: nowrap;
}

.prose-advisory :deep(.advisory-th) {
  background: rgb(30 27 75 / 0.6);
  color: rgb(199 210 254);
  font-weight: 600;
}

.prose-advisory :deep(.advisory-td) {
  color: rgb(224 231 255 / 0.95);
}

.prose-advisory :deep(.advisory-table tbody tr:nth-child(even)) {
  background: rgb(30 27 75 / 0.25);
}
</style>
