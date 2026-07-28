<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import type { ReviewQueueItem } from '../services/cloudseaLabel'
import {
  curateLocationApi,
  fetchReviewQueue,
  labelStatusText,
  reviewLabelApi,
  trainModelApi,
} from '../services/cloudseaLabel'

const props = defineProps<{
  token: string
}>()

const emit = defineEmits<{
  openLabel: [item: ReviewQueueItem]
  refreshed: []
}>()

const message = useMessage()
const loading = ref(false)
const training = ref(false)
const items = ref<ReviewQueueItem[]>([])
const trainResult = ref<{
  status?: string
  holdout_balanced_accuracy?: number
  holdout_recall?: number
  holdout_brier?: number
  deploy_recommended?: boolean
  reason?: string
} | null>(null)

const pendingCount = computed(() => items.value.length)

const locationGroups = computed(() => {
  const map = new Map<string, { name: string; locationId: string; count: number }>()
  for (const item of items.value) {
    const locId = item.location_id || `${item.spot_id}/${item.viewpoint_id}`
    const name = item.location_name || item.community_name || locId
    const prev = map.get(locId)
    map.set(locId, { name, locationId: item.location_id || '', count: (prev?.count || 0) + 1 })
  }
  return [...map.values()]
})

async function loadQueue() {
  if (!props.token) return
  loading.value = true
  try {
    items.value = await fetchReviewQueue(props.token)
  } catch (err) {
    message.error(String(err))
  } finally {
    loading.value = false
  }
}

async function review(item: ReviewQueueItem, status: 'approved' | 'rejected') {
  try {
    await reviewLabelApi(props.token, item.id, status)
    message.success(status === 'approved' ? '已通过' : '已驳回')
    await loadQueue()
    emit('refreshed')
  } catch (err) {
    message.error(String(err))
  }
}

async function curate(locationId: string) {
  try {
    const res = await curateLocationApi(props.token, locationId)
    message.success(`已落库为精选景区：${res.spot_id}`)
  } catch (err) {
    message.error(String(err))
  }
}

async function train() {
  training.value = true
  trainResult.value = null
  try {
    const res = await trainModelApi(props.token)
    trainResult.value = res
    if (res.deploy_recommended) {
      message.success(
        `候选通过时间外评估：平衡准确率 ${((res.holdout_balanced_accuracy || 0) * 100).toFixed(1)}%`,
      )
    } else {
      message.warning(res.reason || '训练完成，但未达部署门槛')
    }
  } catch (err) {
    message.error(String(err))
  } finally {
    training.value = false
  }
}

function openItem(item: ReviewQueueItem) {
  emit('openLabel', item)
}

watch(() => props.token, loadQueue, { immediate: true })
</script>

<template>
  <div class="rounded-xl border border-amber-800/50 bg-amber-950/20 p-4 space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div>
        <div class="font-semibold text-amber-100">Admin · 审核与候选模型</div>
        <div class="text-xs text-slate-400">待审 {{ pendingCount }} 条 · 通过后纳入 ML 训练集</div>
      </div>
      <div class="flex gap-2">
        <n-button size="small" :loading="loading" @click="loadQueue">刷新队列</n-button>
        <n-button size="small" type="warning" :loading="training" @click="train">生成受审候选</n-button>
      </div>
    </div>

    <div v-if="trainResult" class="rounded-lg bg-slate-900/60 p-3 text-xs text-slate-300">
      时间外平衡准确率：{{
        trainResult.holdout_balanced_accuracy != null
          ? (trainResult.holdout_balanced_accuracy * 100).toFixed(1) + '%'
          : '—'
      }}
      · 召回率 {{
        trainResult.holdout_recall != null ? (trainResult.holdout_recall * 100).toFixed(1) + '%' : '—'
      }}
      · Brier {{ trainResult.holdout_brier?.toFixed(3) ?? '—' }}
      <div class="mt-1 text-slate-500">{{ trainResult.reason || '候选未达门禁' }}</div>
    </div>

    <div v-if="locationGroups.length" class="flex flex-wrap gap-2 text-xs">
      <span class="text-slate-500">待审点位：</span>
      <n-tag v-for="g in locationGroups" :key="g.name" size="small" type="warning">
        {{ g.name }} ({{ g.count }})
      </n-tag>
    </div>

    <n-spin :show="loading">
      <div v-if="!items.length" class="text-sm text-slate-500 py-4 text-center">暂无待审标注</div>
      <div v-else class="max-h-72 overflow-y-auto space-y-2">
        <div
          v-for="item in items"
          :key="item.id"
          class="flex flex-wrap items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/50 px-3 py-2 text-sm"
        >
          <button class="text-left text-sky-300 hover:underline" @click="openItem(item)">
            {{ item.location_name || item.community_name || item.viewpoint_id }}
            · {{ item.date }}
            · {{ labelStatusText(item.status) }}
          </button>
          <span class="text-xs text-slate-500">{{ item.location_id || `${item.spot_id}/${item.viewpoint_id}` }}</span>
          <div class="ml-auto flex gap-1">
            <n-button size="tiny" type="success" @click="review(item, 'approved')">通过</n-button>
            <n-button size="tiny" type="error" @click="review(item, 'rejected')">驳回</n-button>
            <n-button
              v-if="item.location_id"
              size="tiny"
              quaternary
              @click="curate(item.location_id!)"
            >
              落库
            </n-button>
          </div>
        </div>
      </div>
    </n-spin>

    <div class="text-[11px] text-slate-500 leading-relaxed">
      社区点位标注免审核、首次保存即自动落库精选。「落库」手动按钮用于补写。候选模型只使用决策时已发布的历史预报，并通过时间外验证；生产不热加载模型，候选须经代码审查后随不可变镜像发布。
    </div>
  </div>
</template>
