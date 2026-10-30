<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  displayDate,
  localInput,
  post,
  type DeadlineChange,
  type DeadlinePreview,
  type InboxItem,
} from '@/api'
import { useCampus } from '@/stores/campus'
const props = defineProps<{ item: InboxItem }>()
const store = useCampus()
const router = useRouter()
const targetId = ref('')
const due = ref('')
const preview = ref<DeadlinePreview | null>(null)
const previewing = ref(false)
const error = ref('')
const active = computed(() => store.tasks.filter((t) => ['todo', 'in_progress'].includes(t.status)))
const candidates = computed(() =>
  props.item.matched_tasks.filter((t) => ['todo', 'in_progress'].includes(t.status)),
)
const others = computed(() =>
  active.value.filter((t) => !candidates.value.some((c) => c.id === t.id)),
)
const target = computed(() => active.value.find((t) => t.id === targetId.value))
const manualDue = computed(() => due.value !== localInput(props.item.fields.due_at ?? null))
const stale = computed(() => preview.value && target.value?.version !== preview.value.task.version)
watch(
  () => [props.item.id, props.item.updated_at],
  () => {
    targetId.value = candidates.value.length === 1 ? candidates.value[0]!.id : ''
    due.value = localInput(props.item.fields.due_at ?? null)
    preview.value = null
    error.value = ''
  },
  { immediate: true },
)
watch([targetId, due], () => {
  preview.value = null
  error.value = ''
})
async function createPreview() {
  if (!targetId.value || !due.value) return
  previewing.value = true
  error.value = ''
  preview.value = null
  const id = props.item.id
  const revision = props.item.updated_at
  const selection = targetId.value
  const proposed = due.value
  try {
    const result = await post<DeadlinePreview>(`/suggestions/${id}/deadline-preview`, {
      task_id: selection,
      due_at: new Date(proposed).toISOString(),
    })
    if (
      props.item.id === id &&
      props.item.updated_at === revision &&
      targetId.value === selection &&
      due.value === proposed
    )
      preview.value = result
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    previewing.value = false
  }
}
async function confirm() {
  if (!preview.value || stale.value) return
  const proposal = preview.value
  const result = await store.mutate(
    () => post<DeadlineChange>(`/deadline-proposals/${proposal.id}/confirm`),
    '截止时间已更新，来源和历史记录已保留。',
  )
  if (result) {
    if (result.reverted) store.notice = '这次更新此前已撤销，没有重复执行。'
    else store.registerDeadlineUndo(result)
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
    <p class="ci-notice ci-before">先核对要更新的任务，再比较新旧时间。确认前不会修改任务。</p>
    <div v-if="!active.length" class="ci-change">
      还没有待办或进行中的任务可关联。请先创建原任务，或忽略这条建议。
    </div>
    <form v-else @submit.prevent="createPreview">
      <label class="ci-field"
        >要更新哪项任务？
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
      <p v-if="candidates.length > 1" class="ci-notice ci-before">
        找到多个可能的任务，需要你选择；系统不会自动应用。
      </p>
      <p v-else-if="!candidates.length" class="ci-notice ci-before">
        暂时没有明确匹配，可以从已有任务中手动选择。
      </p>
      <label class="ci-field"
        >新的截止时间
        <input
          v-model="due"
          type="datetime-local"
          class="ci-input"
          required
          :disabled="store.busy || previewing"
        />
        <small class="ci-evidence">{{
          item.evidence.due_at?.quote
            ? `${manualDue ? '已手动调整，请核对。原文：' : '原文依据：'}${item.evidence.due_at.quote}`
            : '请根据原文明确填写具体日期和时刻，不默认补成当天结束。'
        }}</small>
      </label>
      <button
        class="ci-btn cursor-interaction"
        :disabled="store.busy || previewing || !targetId || !due"
      >
        {{ previewing ? '正在核对…' : preview ? '重新预览 ↻' : '预览这次变更 →' }}
      </button>
    </form>
    <section v-if="preview" class="ci-deadline-preview" aria-label="截止时间变更预览">
      <h3>🗓️ {{ preview.task.title }}</h3>
      <p class="ci-notice ci-before">{{ preview.task.course || '待分类' }}</p>
      <dl class="ci-datecomparison">
        <div>
          <dt>原截止时间</dt>
          <dd>{{ displayDate(preview.task.due_at, true) }}</dd>
        </div>
        <div>
          <dt>建议改为</dt>
          <dd>{{ displayDate(preview.proposed_due_at, true) }}</dd>
        </div>
      </dl>
      <ul v-if="preview.warnings.length" class="ci-warnings">
        <li v-for="warning in preview.warnings" :key="warning">{{ warning }}</li>
      </ul>
      <p class="ci-notice ci-before">本次仅更新截止时间，原通知和其他任务内容都会保留。</p>
      <p v-if="stale" class="ci-inlineerror" role="alert">
        任务在预览后有新变化，请重新预览后确认。
      </p>
      <button
        class="ci-btn ci-primary cursor-interaction"
        :disabled="store.busy || !!stale"
        @click="confirm"
      >
        {{ store.busy ? '正在更新…' : '确认仅更新截止时间 ✓' }}
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
