<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  displayDeadline,
  emptyDraft,
  fieldNames,
  post,
  request,
  taskInput,
  type Task,
  type TaskDetail,
  type TaskInput,
  type TaskRecognitionPreview,
} from '@/api'
import { useCampus } from '@/stores/campus'
import AppModal from './AppModal.vue'
import RecognitionBadge from './RecognitionBadge.vue'

const props = defineProps<{ task: Task }>()
const emit = defineEmits<{ close: [] }>()
const store = useCampus()
const detail = ref<TaskDetail | null>(null)
const preview = ref<TaskRecognitionPreview | null>(null)
const sourceId = ref('')
const selected = ref<string[]>([])
const loading = ref(false)
const error = ref('')

function display(key: string, value: unknown) {
  if (key === 'kind')
    return (
      { homework: '作业 / 实验', exam: '考试', event: '活动', other: '其他' }[String(value)] ||
      String(value)
    )
  return value ? String(value) : '未填写'
}

const differences = computed(() => {
  const result = preview.value
  if (!result) return []
  const items: { key: string; label: string; before: string; after: string }[] = []
  for (const key of Object.keys(emptyDraft()) as (keyof TaskInput)[]) {
    if (key === 'due_at' || key === 'due_date') continue
    const value = result.fields[key]
    if (!value || value === result.task[key] || (key === 'kind' && value === 'other')) continue
    items.push({
      key,
      label: fieldNames[key] || key,
      before: display(key, result.task[key]),
      after: display(key, value),
    })
  }
  const date = {
    due_at: result.fields.due_at ?? null,
    due_date: result.fields.due_date ?? null,
  }
  const prior = result.task.due_at ? Date.parse(result.task.due_at) : result.task.due_date || ''
  const proposed = date.due_at ? Date.parse(date.due_at) : date.due_date || ''
  if (proposed && proposed !== prior)
    items.push({
      key: 'deadline',
      label: '截止时间',
      before: displayDeadline(result.task, true),
      after: displayDeadline(date, true),
    })
  return items
})

async function recognize() {
  loading.value = true
  preview.value = null
  selected.value = []
  error.value = ''
  try {
    detail.value = await request<TaskDetail>(`/tasks/${props.task.id}`)
    sourceId.value ||= detail.value.source.id
    preview.value = await post<TaskRecognitionPreview>(`/tasks/${props.task.id}/recognize`, {
      expected_version: detail.value.task.version,
      message_id: sourceId.value,
    })
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    loading.value = false
  }
}

function changeSource() {
  preview.value = null
  selected.value = []
}

async function confirm() {
  const result = preview.value
  if (!result || !selected.value.length || loading.value || store.busy) return
  const fields = taskInput(result.task)
  for (const key of selected.value) {
    if (key === 'deadline') {
      fields.due_at = result.fields.due_at ?? null
      fields.due_date = result.fields.due_date ?? null
    } else {
      Object.assign(fields, { [key]: result.fields[key as keyof TaskInput] })
    }
  }
  const saved = await store.mutate(
    () =>
      request<Task>(`/tasks/${result.task.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          ...fields,
          status: result.task.status,
          expected_version: result.task.version,
        }),
      }),
    '已采用勾选的识别结果，原任务的修改历史已保存。',
  )
  if (saved) emit('close')
  else {
    error.value = store.error
    store.error = ''
  }
}

onMounted(recognize)
</script>

<template>
  <AppModal title="↻ 重新识别任务" :locked="store.busy" @close="emit('close')">
    <h3>{{ preview?.task.title || detail?.task.title || task.title }}</h3>
    <p class="ci-notice ci-before">从原始通知重新提取信息，核对新旧值并勾选要采用的字段。</p>
    <label v-if="detail && detail.sources.length > 1" class="ci-field">
      识别哪条原始通知？
      <select
        v-model="sourceId"
        class="ci-input"
        :disabled="loading || store.busy"
        @change="changeSource"
      >
        <option v-for="source in detail.sources" :key="source.id" :value="source.id">
          {{ source.source }} · {{ source.text.slice(0, 60) }}
        </option>
      </select>
    </label>
    <p v-if="loading" class="ci-notice" role="status">正在重新识别，请稍候…</p>
    <template v-if="preview">
      <details class="ci-detailsection">
        <summary class="cursor-interaction">查看本次识别的原文</summary>
        <p class="ci-description">{{ preview.message.text }}</p>
      </details>
      <RecognitionBadge
        :method="preview.method"
        :evidence="preview.evidence"
        :message="preview.message"
      />
      <ul v-if="preview.warnings.length" class="ci-warnings">
        <li v-for="warning in preview.warnings" :key="warning">{{ warning }}</li>
      </ul>
      <div v-for="item in differences" :key="item.key" class="ci-merge-field">
        <label class="ci-checklabel">
          <input v-model="selected" type="checkbox" :value="item.key" :disabled="store.busy" />
          采用{{ item.label }}
        </label>
        <dl class="ci-merge-comparison">
          <div>
            <dt>当前值</dt>
            <dd>{{ item.before }}</dd>
          </div>
          <div>
            <dt>识别建议</dt>
            <dd>{{ item.after }}</dd>
          </div>
        </dl>
      </div>
      <p v-if="!differences.length" class="ci-notice">本次未发现新的字段建议。</p>
    </template>
    <p v-if="error" class="ci-inlineerror" role="alert">{{ error }}</p>
    <div class="ci-actions">
      <button
        type="button"
        class="ci-btn cursor-interaction"
        :disabled="loading || store.busy"
        @click="recognize"
      >
        重新识别所选来源
      </button>
      <button
        type="button"
        class="ci-btn ci-primary cursor-interaction"
        :disabled="loading || store.busy || !selected.length"
        @click="confirm"
      >
        {{ store.busy ? '正在保存…' : '确认更新勾选字段 ✓' }}
      </button>
    </div>
  </AppModal>
</template>
