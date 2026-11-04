<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  deadlineExpired,
  deadlineOrder,
  displayDeadline,
  post,
  request,
  statusNames,
  type Task,
  type TaskDetail,
} from '@/api'
import { useCampus } from '@/stores/campus'
import AppModal from './AppModal.vue'

const emit = defineEmits<{ close: [] }>()
const store = useCampus()
const question = ref('')
const asked = ref('')
const modelAnswer = ref<{ answer: string; tasks: Task[]; limited: boolean } | null>(null)
const modelBusy = ref(false)
const modelError = ref('')
const courses = computed(() => [...new Set(store.tasks.map((task) => task.course).filter(Boolean))])
const modelCourse = ref('')
const materialTaskId = ref('')
const materialDetail = ref<TaskDetail | null>(null)
const materialError = ref('')
let materialRequest = 0
function clearMaterials() {
  materialRequest++
  materialTaskId.value = ''
  materialDetail.value = null
  materialError.value = ''
}
async function showMaterials(taskId: string) {
  clearMaterials()
  const version = materialRequest
  materialTaskId.value = taskId
  try {
    const detail = await request<TaskDetail>(`/tasks/${encodeURIComponent(taskId)}`)
    if (version === materialRequest) materialDetail.value = detail
  } catch (cause) {
    if (version === materialRequest) materialError.value = store.errorMessage(cause)
  }
}
function ruleQuery(value: string) {
  clearMaterials()
  question.value = value
  asked.value = value.trim()
  modelAnswer.value = null
  modelError.value = ''
}
async function askModel() {
  const value = question.value.trim()
  if (!value || modelBusy.value) return
  clearMaterials()
  modelBusy.value = true
  modelError.value = ''
  modelAnswer.value = null
  asked.value = ''
  try {
    modelAnswer.value = await post('/assistant/ask', { question: value, course: modelCourse.value })
  } catch (cause) {
    modelError.value = store.errorMessage(cause)
  } finally {
    modelBusy.value = false
  }
}
const examples = ['今天到期', '明天到期', '本周待办', '尚未提交', '已逾期', '已提交']
const formatter = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' })
const active = new Set(['todo', 'in_progress', 'done'])
const result = computed(() => {
  const query = asked.value
  if (!query) return null
  const course = [...courses.value]
    .sort((a, b) => b.length - a.length)
    .find((name) => query.includes(name))
  const phase = query.includes('未提交')
    ? 'active'
    : query.includes('已提交')
      ? 'submitted'
      : query.includes('已做完')
        ? 'done'
        : query.includes('逾期')
          ? 'overdue'
          : 'active'
  const range = query.includes('明天')
    ? 'tomorrow'
    : query.includes('本周')
      ? 'week'
      : query.includes('今天')
        ? 'today'
        : ''
  if (!course && !range && !/待办|任务|逾期|提交|做完/.test(query))
    return { unsupported: true, tasks: [] }
  const today = formatter.format(Date.now())
  const local = new Date(today + 'T00:00:00Z')
  const tomorrow = new Date(local.getTime() + 86400000).toISOString().slice(0, 10)
  const sunday = new Date(local.getTime() + ((7 - local.getUTCDay()) % 7) * 86400000)
    .toISOString()
    .slice(0, 10)
  const tasks = store.tasks
    .filter((task) => {
      if (course && task.course !== course) return false
      if (phase === 'submitted' || phase === 'done') {
        if (task.status !== phase) return false
      } else if (!active.has(task.status)) return false
      if (query.includes('未提交') && deadlineExpired(task, Date.now())) return false
      if (phase === 'overdue' && !deadlineExpired(task, Date.now())) return false
      if (!range) return true
      const date = task.due_date || (task.due_at ? formatter.format(new Date(task.due_at)) : '')
      if (range === 'tomorrow') return date === tomorrow
      if (range === 'today') return date === today
      return date >= today && date <= sunday
    })
    .sort((a, b) => deadlineOrder(a) - deadlineOrder(b))
  return { unsupported: false, tasks }
})
</script>

<template>
  <AppModal title="课务助手" @close="emit('close')">
    <form class="ci-form" @submit.prevent="ruleQuery(question)">
      <label class="ci-field">
        想查什么任务
        <input
          v-model="question"
          class="ci-input"
          maxlength="300"
          required
          :disabled="modelBusy"
          placeholder="例如：软件工程本周待办"
        />
      </label>
      <div class="ci-actions">
        <button type="submit" class="ci-btn ci-primary" :disabled="modelBusy">查询任务</button>
        <button
          v-for="example in examples"
          :key="example"
          type="button"
          class="ci-btn ci-text"
          :disabled="modelBusy"
          @click="ruleQuery(example)"
        >
          {{ example }}
        </button>
      </div>
      <label class="ci-field">
        模型问答范围
        <select v-model="modelCourse" class="ci-input" :disabled="modelBusy">
          <option value="">全部课程</option>
          <option v-for="course in courses" :key="course">{{ course }}</option>
        </select>
      </label>
      <button
        type="button"
        class="ci-btn"
        :disabled="modelBusy || !question.trim()"
        @click="askModel"
      >
        {{ modelBusy ? '本地模型正在回答…' : '问本地模型' }}
      </button>
    </form>
    <p v-if="modelError" class="ci-inlineerror" role="alert">{{ modelError }}</p>
    <template v-if="modelAnswer">
      <p class="ci-proof" role="status">{{ modelAnswer.answer }}</p>
      <p class="ci-notice">
        {{
          modelAnswer.limited
            ? '模型只查看了此范围内最近更新的部分任务。'
            : '请对照引用的任务详情核对回答。'
        }}
      </p>
      <ul v-if="modelAnswer.tasks.length">
        <li v-for="task in modelAnswer.tasks" :key="task.id">
          <RouterLink :to="`/tasks/${task.id}`" @click="emit('close')">{{ task.title }}</RouterLink>
          · {{ statusNames[task.status] }} · {{ displayDeadline(task, true) }}
        </li>
      </ul>
    </template>
    <template v-if="result">
      <p class="ci-notice" role="status">
        {{
          result.unsupported
            ? '可查询课程、截止范围或任务状态。'
            : `找到 ${result.tasks.length} 项任务`
        }}
      </p>
      <ul v-if="result.tasks.length">
        <li v-for="task in result.tasks.slice(0, 30)" :key="task.id">
          <RouterLink :to="`/tasks/${task.id}`" @click="emit('close')">{{ task.title }}</RouterLink>
          · {{ statusNames[task.status] }} · {{ displayDeadline(task, true) }}
          <button type="button" class="ci-btn ci-text" @click="showMaterials(task.id)">
            查看材料
          </button>
          <template v-if="materialTaskId === task.id">
            <p v-if="materialError" class="ci-inlineerror" role="alert">{{ materialError }}</p>
            <template v-else-if="materialDetail">
              <p v-if="materialDetail.task.file_requirements">
                文件要求：{{ materialDetail.task.file_requirements }}
              </p>
              <ul v-if="materialDetail.materials.length">
                <li v-for="(item, index) in materialDetail.materials" :key="index">
                  {{ item.done ? '已准备' : '待准备' }} · {{ item.text }}
                </li>
              </ul>
              <p v-else>还没有保存材料清单。</p>
            </template>
            <p v-else role="status">正在读取材料…</p>
          </template>
        </li>
      </ul>
      <p v-if="result.tasks.length > 30" class="ci-notice">
        显示最靠前的 30 项，可按课程或日期缩小范围。
      </p>
    </template>
  </AppModal>
</template>
