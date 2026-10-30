<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  displayDate,
  post,
  statusNames,
  type CancellationPreview,
  type DeadlineChange,
  type InboxItem,
} from '@/api'
import { useCampus } from '@/stores/campus'
const props = defineProps<{ item: InboxItem }>()
const store = useCampus()
const router = useRouter()
const targetId = ref('')
const preview = ref<CancellationPreview | null>(null)
const previewing = ref(false)
const error = ref('')
const active = computed(() => store.tasks.filter((t) => ['todo', 'in_progress'].includes(t.status)))
const candidates = computed(() =>
  props.item.matched_tasks.filter((t) => ['todo', 'in_progress'].includes(t.status)),
)
const others = computed(() =>
  active.value.filter((t) => !candidates.value.some((c) => c.id === t.id)),
)
const stale = computed(
  () =>
    preview.value &&
    active.value.find((t) => t.id === targetId.value)?.version !== preview.value.task.version,
)
watch(
  () => [props.item.id, props.item.updated_at],
  () => {
    targetId.value = candidates.value.length === 1 ? candidates.value[0]!.id : ''
    preview.value = null
    error.value = ''
  },
  { immediate: true },
)
watch(targetId, () => {
  preview.value = null
  error.value = ''
})
async function createPreview() {
  if (!targetId.value) return
  previewing.value = true
  error.value = ''
  preview.value = null
  const id = props.item.id
  const revision = props.item.updated_at
  const selection = targetId.value
  try {
    const result = await post<CancellationPreview>(`/suggestions/${id}/cancellation-preview`, {
      task_id: selection,
    })
    if (props.item.id === id && props.item.updated_at === revision && targetId.value === selection)
      preview.value = result
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    previewing.value = false
  }
}
async function confirm() {
  if (!preview.value || stale.value) return
  const result = await store.mutate(
    () => post<DeadlineChange>(`/cancellation-proposals/${preview.value!.id}/confirm`),
    '任务已取消，原通知和任务内容已保留。',
  )
  if (result) {
    if (result.reverted) store.notice = '这次取消此前已撤销，没有重复执行。'
    else store.registerCancellationUndo(result)
    await router.push(`/tasks/${result.task.id}`)
  } else {
    error.value = store.error
    store.error = ''
    preview.value = null
  }
}
async function refreshContext() {
  await store.initialize()
  error.value = store.error
  store.error = ''
}
</script>
<template>
  <div class="ci-deadline-review">
    <p class="ci-notice ci-before">
      核对通知取消的是哪项任务。确认后任务会标记为已取消，可在任务详情撤销。
    </p>
    <p v-if="!active.length" class="ci-change">还没有待办或进行中的任务可关联，请先核对原任务。</p>
    <form v-else @submit.prevent="createPreview">
      <label class="ci-field"
        >要取消哪项任务？
        <select v-model="targetId" class="ci-input" required :disabled="store.busy || previewing">
          <option value="" disabled>选择目标任务，请核对课程和名称</option>
          <optgroup v-if="candidates.length" label="可能关联的任务">
            <option v-for="task in candidates" :key="task.id" :value="task.id">
              {{ task.course || '待分类' }} · {{ task.title }}
            </option>
          </optgroup>
          <optgroup v-if="others.length" label="其他待办 / 进行中的任务">
            <option v-for="task in others" :key="task.id" :value="task.id">
              {{ task.course || '待分类' }} · {{ task.title }}
            </option>
          </optgroup>
        </select>
      </label>
      <p v-if="candidates.length !== 1" class="ci-notice ci-before">
        {{
          candidates.length
            ? '存在多个可能的任务，请明确选择。'
            : '没有明确匹配，请从已有任务中手动选择。'
        }}
      </p>
      <button class="ci-btn cursor-interaction" :disabled="store.busy || previewing || !targetId">
        {{ previewing ? '正在核对…' : '预览取消结果 →' }}
      </button>
    </form>
    <section v-if="preview" class="ci-deadline-preview" aria-label="取消任务预览">
      <h3>📌 {{ preview.task.title }}</h3>
      <p class="ci-notice ci-before">
        {{ preview.task.course || '待分类' }} · {{ displayDate(preview.task.due_at, true) }}
      </p>
      <dl class="ci-datecomparison">
        <div>
          <dt>当前状态</dt>
          <dd>{{ statusNames[preview.task.status] }}</dd>
        </div>
        <div>
          <dt>确认后</dt>
          <dd>已取消</dd>
        </div>
      </dl>
      <p class="ci-notice ci-before">原任务内容、截止时间及所有来源都会保留。</p>
      <p v-if="stale" class="ci-inlineerror" role="alert">任务在预览后有变化，请刷新后重新预览。</p>
      <button
        class="ci-btn ci-primary cursor-interaction"
        :disabled="store.busy || !!stale"
        @click="confirm"
      >
        {{ store.busy ? '正在保存…' : '确认取消这项任务 ✓' }}
      </button>
    </section>
    <div v-if="error" class="ci-inlineerror" role="alert">
      {{ error
      }}<button
        class="ci-btn ci-text cursor-interaction"
        :disabled="store.loading"
        @click="refreshContext"
      >
        刷新任务与建议 ↻
      </button>
    </div>
  </div>
</template>
