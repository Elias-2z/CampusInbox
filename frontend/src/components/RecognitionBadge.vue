<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  displayMessageTime,
  fieldNames,
  request,
  type Evidence,
  type Message,
  type TaskDetail,
} from '@/api'

const props = defineProps<{
  method: string
  taskId?: string
  evidence?: Record<string, Evidence>
  message?: Message
  warnings?: string[]
}>()
const dialog = ref<HTMLDialogElement | null>(null)
const detail = ref<TaskDetail | null>(null)
const loading = ref(false)
const error = ref('')
const activeMethod = computed(() => detail.value?.method || props.method)
const kind = computed(() => {
  if (['mixed'].includes(activeMethod.value)) return 'mixed'
  if (['llm', 'model'].includes(activeMethod.value)) return 'model'
  if (activeMethod.value === 'rule') return 'rule'
  if (activeMethod.value === 'manual') return 'manual'
  if (activeMethod.value === 'legacy') return 'legacy'
  return 'unknown'
})
const names = {
  rule: '规则识别',
  mixed: '规则＋模型',
  model: '模型识别',
  manual: '手动创建',
  legacy: '旧版导入',
  unknown: '识别记录',
}
const data = computed(() => detail.value?.evidence || props.evidence || {})
const source = computed(() => detail.value?.source || props.message)
const rows = computed(() => Object.entries(data.value).filter(([key]) => key in fieldNames))
function evidenceName(value: Evidence): string {
  if (value.method === 'user') return '用户确认 / 修改'
  if (value.method === 'connector') return '平台来源'
  if (value.method === 'rule') return '规则提取'
  if (value.method === 'llm+date') return '模型提取＋日期运算'
  if (['llm', 'model'].includes(value.method)) return '模型提取'
  if (value.method === 'attachment') return '附件提取'
  return '来源记录'
}
async function open() {
  dialog.value?.showModal()
  error.value = ''
  if (!props.taskId) return
  loading.value = true
  try {
    detail.value = await request<TaskDetail>(`/tasks/${props.taskId}`)
  } catch {
    error.value = '暂时无法读取识别依据，请稍后重试。'
  } finally {
    loading.value = false
  }
}
function backdrop(event: MouseEvent) {
  if (event.target === dialog.value) dialog.value?.close()
}
</script>
<template>
  <button
    type="button"
    class="ci-recognition-badge cursor-interaction"
    :data-kind="kind"
    :aria-label="`${names[kind]}：查看识别说明`"
    @click.stop="open"
  >
    <span aria-hidden="true">{{
      kind === 'mixed' || kind === 'model' ? '✦' : kind === 'rule' ? '⌘' : '✎'
    }}</span>
    {{ names[kind] }} <span aria-hidden="true" class="ci-recognition-info">ⓘ</span>
  </button>
  <dialog
    ref="dialog"
    class="ci-recognition-dialog"
    aria-label="识别方式与字段依据"
    @click="backdrop"
  >
    <div class="ci-sectionhead">
      <h2>这条任务是怎么识别的？</h2>
      <button
        type="button"
        class="ci-btn ci-text"
        autofocus
        aria-label="关闭识别说明"
        @click="dialog?.close()"
      >
        关闭 ✕
      </button>
    </div>
    <p class="ci-recognition-heading">{{ names[kind] }}</p>
    <p v-if="loading" role="status">正在读取字段依据…</p>
    <p v-if="error" role="alert" class="ci-inlineerror">{{ error }}</p>
    <ol v-if="kind === 'rule' || kind === 'mixed' || kind === 'model'" class="ci-recognition-steps">
      <li v-if="kind !== 'model'">
        <b>规则提取</b>
        <p>匹配任务关键词与日期表达，结合消息发送时间和时区计算相对日期。</p>
      </li>
      <li v-if="kind === 'mixed' || kind === 'model'">
        <b>模型补充</b>
        <p>从通知原文提取规则未确定的字段；程序核对原文依据，并统一解析时间。</p>
      </li>
      <li>
        <b>用户审核</b>
        <p>确认后进入任务管理，缺失或冲突的信息由用户核对。</p>
      </li>
    </ol>
    <p v-else-if="kind === 'manual'">这条任务由用户手动创建，字段由用户填写和确认。</p>
    <p v-else-if="kind === 'legacy'">这条任务来自旧版数据导入，原始识别方式以保留的记录为准。</p>
    <p v-else>这条记录未保存明确的识别方式，请以原文和字段依据为准。</p>
    <p v-if="kind === 'rule'" class="ci-notice">
      当前记录未采用模型补充字段。模型可能未调用，或未返回可采用的结果。
    </p>
    <p v-if="kind === 'model'" class="ci-notice">
      此标签来自模型识别记录；本项目通常采用规则优先、模型按需补充的流程。
    </p>
    <p v-if="source" class="ci-recognition-anchor">
      消息发送于 {{ displayMessageTime(source, true) }} · {{ source.timezone
      }}<br />相对日期以此时间为基准；没有明确时刻时只保留日期。
    </p>
    <div v-if="rows.length" class="ci-recognition-fields">
      <h3>字段与原文依据</h3>
      <p class="ci-muted">识别方式记录初始处理路径；下列依据反映后续确认与修改。</p>
      <div v-for="[key, value] in rows" :key="key" class="ci-recognition-field">
        <div>
          <b>{{ fieldNames[key] || key }}</b
          ><span>{{ evidenceName(value) }}</span>
        </div>
        <blockquote>{{ value.quote }}</blockquote>
        <small v-if="value.method === 'user' && value.original"
          >原识别：{{ evidenceName(value.original) }} · {{ value.original.quote }}</small
        >
      </div>
    </div>
    <p v-else-if="!loading && !error" class="ci-muted">这条记录没有可展示的逐字段依据。</p>
    <p v-for="warning in (warnings || []).slice(0, 3)" :key="warning" class="ci-notice">
      {{ warning }}
    </p>
  </dialog>
</template>
