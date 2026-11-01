<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  displayDate,
  fieldNames,
  request,
  statusNames,
  type TaskDetail,
  type TaskEvent,
} from '@/api'
import { useCampus } from '@/stores/campus'
import TaskList from '@/components/TaskList.vue'
const store = useCampus()
const route = useRoute()
const status = ref('active')
const course = ref('')
const search = ref('')
const info = ref<TaskDetail | null>(null)
const detailError = ref('')
const detailLoading = ref(false)
const selectedVersion = computed(() => store.tasks.find((t) => t.id === route.params.id)?.version)
const courses = computed(() => [...new Set(store.tasks.map((t) => t.course).filter(Boolean))])
const filtered = computed(() =>
  store.tasks.filter(
    (t) =>
      (status.value === 'all'
        ? t.status !== 'archived'
        : status.value === 'active'
          ? ['todo', 'in_progress'].includes(t.status)
          : t.status === status.value) &&
      (!course.value || t.course === course.value) &&
      t.title.toLowerCase().includes(search.value.trim().toLowerCase()),
  ),
)
let detailRequest = 0
watch(
  () => [route.params.id, selectedVersion.value],
  async () => {
    const current = ++detailRequest
    if (!route.params.id) {
      info.value = null
      detailError.value = ''
      return
    }
    info.value = null
    detailLoading.value = true
    detailError.value = ''
    try {
      const result = await request<TaskDetail>(`/tasks/${route.params.id}`)
      if (current === detailRequest) info.value = result
    } catch (e) {
      if (current === detailRequest) detailError.value = store.errorMessage(e)
    } finally {
      if (current === detailRequest) detailLoading.value = false
    }
  },
  { immediate: true },
)
function changes(event: TaskEvent) {
  return Object.keys(fieldNames).filter((key) => event.before[key] !== event.after[key])
}
function eventValue(key: string, value: unknown) {
  if (!value) return '未填写'
  if (key === 'due_at') return displayDate(String(value), true)
  if (key === 'status') return statusNames[value as keyof typeof statusNames] || String(value)
  if (key === 'kind')
    return (
      { homework: '作业 / 实验', exam: '考试', event: '活动', other: '其他' }[String(value)] ||
      String(value)
    )
  return String(value)
}
</script>
<template>
  <div class="ci-pagehead">
    <div>
      <div class="ci-eyebrow">LESS CHAOS, MORE CAMPUS</div>
      <h1>事情都在掌握中 🗂️</h1>
    </div>
    <button
      class="ci-btn ci-primary cursor-interaction"
      :disabled="!store.ready"
      @click="store.edit()"
    >
      ＋ 新建任务
    </button>
  </div>
  <div class="ci-taskfilters">
    <div class="ci-filters" aria-label="任务状态">
      <button
        v-for="option in [
          { id: 'active', name: '待办与进行中' },
          { id: 'all', name: '全部' },
          { id: 'completed', name: '已完成' },
          { id: 'cancelled', name: '已取消' },
          { id: 'archived', name: '已归档' },
        ]"
        :key="option.id"
        class="ci-filter cursor-interaction"
        :aria-pressed="status === option.id"
        @click="status = option.id"
      >
        {{ option.name }}
      </button>
    </div>
    <div class="ci-searchrow">
      <label class="ci-sr"
        >按课程筛选<select v-model="course" class="ci-input">
          <option value="">全部课程</option>
          <option v-for="name in courses" :key="name">{{ name }}</option>
        </select></label
      ><input
        v-model="search"
        class="ci-input"
        placeholder="搜索任务…"
        aria-label="搜索任务"
        maxlength="160"
      />
    </div>
  </div>
  <div :class="{ 'ci-tasksplit': !!route.params.id }">
    <section>
      <TaskList v-if="store.ready" :tasks="filtered" />
      <p class="ci-notice">截止时间可以待补充；归档可以恢复，不会删除来源记录。</p>
    </section>
    <section v-if="route.params.id" class="ci-detailpanel">
      <div class="ci-sectionhead">
        <h2>任务详情</h2>
        <RouterLink class="ci-btn ci-text cursor-interaction" to="/tasks">关闭 ✕</RouterLink>
      </div>
      <p v-if="detailLoading" class="ci-notice">正在读取任务…</p>
      <p v-else-if="detailError" class="ci-inlineerror" role="alert">{{ detailError }}</p>
      <template v-else-if="info"
        ><h2 class="ci-detailtitle">{{ info.task.title }}</h2>
        <span class="ci-tag">{{ statusNames[info.task.status] }}</span>
        <dl class="ci-detailgrid">
          <div
            v-for="key in [
              'course',
              'due_at',
              'submission_platform',
              'submission_method',
              'file_requirements',
              'location',
            ] as const"
            :key="key"
          >
            <dt>{{ fieldNames[key] }}</dt>
            <dd>
              {{
                key === 'due_at' ? displayDate(info.task.due_at, true) : info.task[key] || '待补充'
              }}
            </dd>
          </div>
        </dl>
        <p v-if="info.task.description" class="ci-description">{{ info.task.description }}</p>
        <p v-if="info.task.remarks" class="ci-notice">备注：{{ info.task.remarks }}</p>
        <div class="ci-actions ci-detailactions">
          <button
            class="ci-btn ci-primary cursor-interaction"
            :disabled="store.busy"
            @click="store.edit(info.task)"
          >
            编辑任务</button
          ><button
            v-if="info.task.status !== 'archived'"
            class="ci-btn cursor-interaction"
            :disabled="store.busy"
            @click="store.archive(info.task)"
          >
            归档</button
          ><button
            v-else
            class="ci-btn cursor-interaction"
            :disabled="store.busy"
            @click="store.changeStatus(info.task, 'todo')"
          >
            恢复为待办
          </button>
        </div>
        <section
          v-for="change in info.deadline_changes"
          :key="change.suggestion_id"
          class="ci-deadline-history"
        >
          <b>{{ change.reverted ? '↶ 截止时间更新已撤销' : '🗓️ 已确认的截止时间更新' }}</b>
          <button
            v-if="change.can_undo"
            class="ci-btn cursor-interaction"
            :disabled="store.busy"
            @click="store.undoDeadline(change)"
          >
            撤销本次截止时间更新 ↶
          </button>
          <p v-else-if="!change.reverted" class="ci-notice">
            更新后已有其他编辑，请核对下方历史后手动调整。
          </p>
        </section>
        <section
          v-for="change in info.cancellation_changes"
          :key="change.suggestion_id"
          class="ci-deadline-history"
        >
          <b>{{ change.reverted ? '↶ 取消已撤销' : '📌 已确认的取消通知' }}</b>
          <button
            v-if="change.can_undo"
            class="ci-btn cursor-interaction"
            :disabled="store.busy"
            @click="store.undoCancellation(change)"
          >
            撤销本次取消 ↶
          </button>
          <p v-else-if="!change.reverted" class="ci-notice">
            取消后已有其他编辑，请核对下方历史后手动调整。
          </p>
        </section>
        <section v-for="change in info.merge_changes" :key="change.id" class="ci-deadline-history">
          <b>{{ change.reverted ? '↶ 关联与字段补充已撤销' : '🔗 已确认的通知关联与补充' }}</b>
          <button
            v-if="change.can_undo"
            class="ci-btn cursor-interaction"
            :disabled="store.busy"
            @click="store.undoMerge(change)"
          >
            撤销本次关联与补充 ↶
          </button>
          <p v-else-if="!change.reverted" class="ci-notice">
            关联后已有其他编辑，请核对历史后人工调整。
          </p>
        </section>
        <details class="ci-detailsection" open>
          <summary class="cursor-interaction">📎 通知来源 · {{ info.sources.length }} 条</summary>
          <article v-for="(source, index) in info.sources" :key="source.id" class="ci-linkedsource">
            <small class="ci-muted"
              >{{ index === 0 ? '创建来源' : '后续通知' }} · {{ source.source }} ·
              {{ displayDate(source.sent_at, true) }}</small
            >
            <p class="ci-proof">{{ source.text }}</p>
            <a
              v-for="attachment in source.attachments"
              :key="attachment.id"
              class="ci-attachmentlink"
              :href="`/api/attachments/${attachment.id}/download`"
              >📎 下载原始附件：{{ attachment.filename }}</a
            >
          </article>
        </details>
        <details class="ci-detailsection">
          <summary class="cursor-interaction">🔍 字段依据</summary>
          <p v-for="(evidence, key) in info.evidence" :key="key" class="ci-evidence">
            <b>{{ fieldNames[key] || key }}</b
            >：{{ evidence.quote }}
            <span
              >（{{
                evidence.method === 'rule'
                  ? '规则'
                  : evidence.method === 'user'
                    ? '用户'
                    : '模型辅助'
              }}）</span
            >
          </p>
        </details>
        <details class="ci-detailsection">
          <summary class="cursor-interaction">🕰️ 修改历史 · {{ info.events.length }} 条</summary>
          <article v-for="event in [...info.events].reverse()" :key="event.id" class="ci-event">
            <b>{{
              event.operation === 'create'
                ? '用户确认创建'
                : event.operation === 'deadline_update'
                  ? '用户确认截止时间变更'
                  : event.operation === 'deadline_undo'
                    ? '撤销截止时间变更'
                    : event.operation === 'cancel'
                      ? '用户确认取消任务'
                      : event.operation === 'cancel_undo'
                        ? '撤销取消任务'
                        : event.operation === 'merge'
                          ? '用户确认通知关联与字段补充'
                          : event.operation === 'merge_undo'
                            ? '撤销通知关联与字段补充'
                            : '用户更新任务'
            }}</b
            ><small
              >{{ displayDate(event.created_at, true) }} ·
              {{
                info.sources.find((source) => source.id === event.message_id)?.source || '用户操作'
              }}</small
            ><template v-if="event.operation !== 'create'"
              ><p
                v-if="!changes(event).length && ['merge', 'merge_undo'].includes(event.operation)"
              >
                仅记录通知关联状态，业务字段无变化。
              </p>
              <p v-for="key in changes(event)" :key="key">
                {{ fieldNames[key] }}：{{ eventValue(key, event.before[key]) }} →
                {{ eventValue(key, event.after[key]) }}
              </p></template
            >
          </article>
        </details>
      </template>
    </section>
  </div>
</template>
