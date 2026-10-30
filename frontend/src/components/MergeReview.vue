<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  displayDate,
  fieldNames,
  post,
  emptyDraft,
  type Evidence,
  type MergeChange,
  type MergePreview,
  type Task,
  type TaskInput,
} from '@/api'
import { useCampus } from '@/stores/campus'
const props = defineProps<{
  source: {
    id: string
    updated_at: string
    fields: Partial<TaskInput>
    evidence: Record<string, Evidence>
    matched_tasks: Task[]
  }
  splitDraft?: boolean
}>()
const store = useCampus()
const router = useRouter()
const targetId = ref('')
const selected = ref<string[]>([])
const preview = ref<MergePreview | null>(null)
const previewing = ref(false)
const error = ref('')
const active = computed(() => store.tasks.filter((t) => ['todo', 'in_progress'].includes(t.status)))
const candidates = computed(() =>
  props.source.matched_tasks.filter((t) => ['todo', 'in_progress'].includes(t.status)),
)
const others = computed(() =>
  active.value.filter((t) => !candidates.value.some((c) => c.id === t.id)),
)
const target = computed(() => active.value.find((t) => t.id === targetId.value))
const stale = computed(() => preview.value && target.value?.version !== preview.value.task.version)
function equal(key: string, a: unknown, b: unknown) {
  if (key === 'due_at' && a && b)
    return new Date(String(a)).valueOf() === new Date(String(b)).valueOf()
  return (a ?? '') === (b ?? '')
}
const differences = computed(() =>
  target.value
    ? (Object.keys(emptyDraft()) as (keyof TaskInput)[]).filter(
        (key) =>
          props.source.fields[key] !== undefined &&
          props.source.fields[key] !== null &&
          props.source.fields[key] !== '' &&
          !equal(key, props.source.fields[key], target.value![key]),
      )
    : [],
)
function display(key: string, value: unknown) {
  if (value === undefined || value === null || value === '') return '未填写'
  if (key === 'due_at') return displayDate(String(value), true)
  if (key === 'kind')
    return (
      { homework: '作业 / 实验', exam: '考试', event: '活动', other: '其他' }[String(value)] ||
      String(value)
    )
  return String(value)
}
watch(
  [() => props.source.id, () => props.source.updated_at],
  () => {
    targetId.value = candidates.value.length === 1 ? candidates.value[0]!.id : ''
    selected.value = []
    preview.value = null
    error.value = ''
  },
  { immediate: true },
)
watch(targetId, () => {
  selected.value = []
  preview.value = null
  error.value = ''
})
watch(
  selected,
  () => {
    preview.value = null
    error.value = ''
  },
  { deep: true },
)
async function createPreview() {
  if (!targetId.value) return
  previewing.value = true
  preview.value = null
  error.value = ''
  const id = props.source.id
  const revision = props.source.updated_at
  const target = targetId.value
  const fields = [...selected.value]
  try {
    const result = await post<MergePreview>(
      `/${props.splitDraft ? 'split-drafts' : 'suggestions'}/${id}/merge-preview`,
      { task_id: target, fields },
    )
    if (
      props.source.id === id &&
      props.source.updated_at === revision &&
      targetId.value === target &&
      JSON.stringify(selected.value) === JSON.stringify(fields)
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
    () => post<MergeChange>(`/merge-proposals/${proposal.id}/confirm`),
    '已关联原任务，并保存本次确认的字段与来源。',
  )
  if (result) {
    if (result.reverted) store.notice = '本次关联此前已撤销，没有重复执行。'
    else store.registerMergeUndo(result)
    if (!props.splitDraft) await router.push(`/tasks/${result.task.id}`)
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
  preview.value = null
}
</script>
<template>
  <section class="ci-merge-review" aria-label="关联已有任务审核">
    <p class="ci-notice ci-before">
      核对为同一项任务后，可只关联通知来源，或勾选要采用的通知字段。未勾选的内容保留原值。
    </p>
    <p v-if="splitDraft" class="ci-notice ci-before">
      这里使用已保存的草稿字段；刚修改过内容时，请先返回保存草稿。
    </p>
    <p v-if="!active.length" class="ci-change">
      暂无待办或进行中的任务可关联，可以返回创建独立任务。
    </p>
    <form v-else @submit.prevent="createPreview">
      <label class="ci-field"
        >关联哪项任务？
        <select v-model="targetId" class="ci-input" required :disabled="store.busy || previewing">
          <option value="" disabled>请核对课程和名称，再选择原任务</option>
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
        有多个可能的任务，需要明确选择。
      </p>
      <template v-if="target">
        <p class="ci-notice ci-before">
          当前截止时间：{{
            displayDate(target.due_at, true)
          }}。字段来自已识别通知；需要进一步修改时，可先只关联，再编辑任务。
        </p>
        <div v-for="key in differences" :key="key" class="ci-merge-field">
          <label class="ci-checklabel"
            ><input
              v-model="selected"
              type="checkbox"
              :value="key"
              :disabled="store.busy || previewing"
            />采用通知中的{{ fieldNames[key] }}</label
          >
          <dl class="ci-merge-comparison">
            <div>
              <dt>原任务</dt>
              <dd>{{ display(key, target[key]) }}</dd>
            </div>
            <div>
              <dt>通知 / 草稿</dt>
              <dd>{{ display(key, source.fields[key]) }}</dd>
            </div>
          </dl>
          <small v-if="source.evidence[key]" class="ci-evidence"
            >依据：{{ source.evidence[key]?.quote }}</small
          >
        </div>
        <p v-if="!differences.length" class="ci-notice ci-before">
          没有可补充的字段差异，本次只关联通知来源。
        </p>
      </template>
      <button class="ci-btn cursor-interaction" :disabled="store.busy || previewing || !targetId">
        {{ previewing ? '正在核对…' : '预览关联与字段变化 →' }}
      </button>
    </form>
    <section v-if="preview" class="ci-deadline-preview" aria-label="关联与合并预览">
      <h3>🔗 {{ preview.task.title }}</h3>
      <p class="ci-notice ci-before">{{ preview.task.course || '待分类' }}</p>
      <div v-for="(value, key) in preview.after_fields" :key="key" class="ci-merge-field">
        <b>{{ fieldNames[key] }}</b>
        <dl class="ci-merge-comparison">
          <div>
            <dt>原值</dt>
            <dd>{{ display(key, preview.before_fields[key]) }}</dd>
          </div>
          <div>
            <dt>确认后</dt>
            <dd>{{ display(key, value) }}</dd>
          </div>
        </dl>
      </div>
      <p v-if="!Object.keys(preview.after_fields).length" class="ci-notice ci-before">
        只关联本条通知来源，任务业务字段保持原值。
      </p>
      <ul v-if="preview.warnings.length" class="ci-warnings">
        <li v-for="warning in preview.warnings" :key="warning">{{ warning }}</li>
      </ul>
      <p class="ci-notice ci-before">
        确认后使用现有任务，不会另外创建任务。可在详情撤销本次关联和补充。
      </p>
      <p v-if="stale" class="ci-inlineerror" role="alert">
        目标任务在预览后有变化，请刷新后重新预览。
      </p>
      <button
        class="ci-btn ci-primary cursor-interaction"
        :disabled="store.busy || !!stale"
        @click="confirm"
      >
        {{ store.busy ? '正在保存…' : '确认关联并应用勾选字段 ✓' }}
      </button>
    </section>
    <div v-if="error" class="ci-inlineerror" role="alert">
      {{ error
      }}<button
        class="ci-btn ci-text cursor-interaction"
        :disabled="store.busy || store.loading"
        @click="refreshContext"
      >
        刷新任务与通知 ↻
      </button>
    </div>
  </section>
</template>
