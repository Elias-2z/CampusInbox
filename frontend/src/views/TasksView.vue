<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  displayDate,
  displayMessageTime,
  displayDeadline,
  fieldNames,
  post,
  request,
  statusNames,
  type TaskDetail,
  type TaskEvent,
} from '@/api'
import { useCampus } from '@/stores/campus'
import TaskList from '@/components/TaskList.vue'
import AttachmentText from '@/components/AttachmentText.vue'
import { addMaterialCandidate, type MaterialCandidate } from '@/materialSuggestions'
const store = useCampus()
const route = useRoute()
const status = ref('active')
const course = ref('')
const search = ref('')
const info = ref<TaskDetail | null>(null)
const materialDraft = ref<{ text: string; done: boolean }[]>([])
const newMaterial = ref('')
const materialSuggestions = ref<MaterialCandidate[]>([])
const materialSuggestionError = ref('')
const suggestingMaterials = ref(false)
const refreshingPlatform = ref(false)
const platformRequest = ref('')
const materialSuggestionsChecked = ref(false)
const detailError = ref('')
const detailLoading = ref(false)
const selectedVersion = computed(() => store.tasks.find((t) => t.id === route.params.id)?.version)
const platformStale = computed(() => {
  const value = info.value?.platform_observation?.checked_at
  return (
    !value ||
    !Number.isFinite(Date.parse(value)) ||
    store.clock - Date.parse(value) > 24 * 60 * 60 * 1000
  )
})
const courses = computed(() => [...new Set(store.tasks.map((t) => t.course).filter(Boolean))])
const filtered = computed(() =>
  store.tasks.filter(
    (t) =>
      (status.value === 'all'
        ? t.status !== 'archived'
        : status.value === 'active'
          ? ['todo', 'in_progress', 'done'].includes(t.status)
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
    platformRequest.value = ''
    materialSuggestions.value = []
    materialSuggestionError.value = ''
    materialSuggestionsChecked.value = false
    detailLoading.value = true
    detailError.value = ''
    try {
      const result = await request<TaskDetail>(`/tasks/${route.params.id}`)
      if (current === detailRequest) {
        info.value = result
        materialDraft.value = result.materials.map((item) => ({ ...item }))
        newMaterial.value = ''
      }
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
  if (key === 'materials' && Array.isArray(value))
    return value.map((item) => `${item.done ? '☑' : '☐'} ${item.text}`).join('、') || '无'
  if (key === 'due_at') return displayDate(String(value), true)
  if (key === 'status') return statusNames[value as keyof typeof statusNames] || String(value)
  if (key === 'kind')
    return (
      { homework: '作业 / 实验', exam: '考试', event: '活动', other: '其他' }[String(value)] ||
      String(value)
    )
  return String(value)
}
function platformEvidence(event: TaskEvent) {
  const value = event.after.platform_observation
  if (!value || typeof value !== 'object' || !('status' in value) || !('checked_at' in value))
    return ''
  if (typeof value.status !== 'string' || typeof value.checked_at !== 'string') return ''
  return `平台当时显示：${value.status || '未能读取'} · 核对时间：${displayDate(value.checked_at, true)}`
}
function addMaterial() {
  const text = newMaterial.value.trim()
  if (!text || materialDraft.value.length >= 100) return
  materialDraft.value.push({ text, done: false })
  newMaterial.value = ''
}
async function saveMaterials() {
  if (!info.value) return
  const task = info.value.task
  await store.mutate(
    () =>
      request<TaskDetail['task']>(`/tasks/${task.id}/materials`, {
        method: 'PUT',
        body: JSON.stringify({ expected_version: task.version, items: materialDraft.value }),
      }),
    '材料清单已保存。',
  )
}
async function refreshPlatform() {
  if (!info.value || refreshingPlatform.value) return
  const taskId = info.value.task.id
  refreshingPlatform.value = true
  try {
    const latest = await request<TaskDetail>(`/tasks/${taskId}`)
    if (info.value?.task.id !== taskId) return
    if (info.value.task.version !== latest.task.version) {
      await store.refresh()
    } else {
      info.value.platform_observation = latest.platform_observation
      info.value.checkin_closed = latest.checkin_closed
    }
  } catch (error) {
    store.error = store.errorMessage(error)
  } finally {
    refreshingPlatform.value = false
  }
}
async function recheckPlatform() {
  if (!info.value || refreshingPlatform.value) return
  const id = info.value.task.id
  refreshingPlatform.value = true
  platformRequest.value = ''
  try {
    await post(`/connectors/chaoxing/tasks/${id}/recheck`)
    if (info.value?.task.id === id) platformRequest.value = '已请求重查，请稍后刷新本地记录。'
  } catch (error) {
    if (info.value?.task.id === id) store.error = store.errorMessage(error)
  } finally {
    refreshingPlatform.value = false
  }
}
async function adoptPlatform() {
  if (!info.value?.platform_observation?.suggested_status) return
  const task = info.value.task
  const observation = info.value.platform_observation
  if (!window.confirm(`确认根据学习通“${observation.status}”更新任务状态？`)) return
  await store.mutate(
    () =>
      post<TaskDetail['task']>(`/tasks/${task.id}/platform-status`, {
        expected_version: task.version,
        observation_id: observation.id,
      }),
    '任务状态已按学习通记录更新。',
  )
}
async function suggestMaterials() {
  if (!info.value || suggestingMaterials.value) return
  const taskId = info.value.task.id
  suggestingMaterials.value = true
  materialSuggestionError.value = ''
  try {
    const result = await request<{ items: MaterialCandidate[] }>(
      `/tasks/${taskId}/material-suggestions`,
    )
    if (info.value?.task.id === taskId) {
      materialSuggestions.value = result.items
      materialSuggestionsChecked.value = true
    }
  } catch (error) {
    if (info.value?.task.id === taskId) materialSuggestionError.value = store.errorMessage(error)
  } finally {
    suggestingMaterials.value = false
  }
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
          { id: 'done', name: '已做完，未提交' },
          { id: 'submitted', name: '已提交' },
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
              {{ key === 'due_at' ? displayDeadline(info.task, true) : info.task[key] || '待补充' }}
            </dd>
          </div>
        </dl>
        <p v-if="info.task.description" class="ci-description">{{ info.task.description }}</p>
        <p v-if="info.task.remarks" class="ci-notice">备注：{{ info.task.remarks }}</p>
        <section
          v-if="
            info.source.source_context?.platform === 'chaoxing' &&
            info.source.source_context.kind === 'checkin_activity'
          "
          class="ci-detailsection"
          aria-label="学习通签到"
        >
          <h3>学习通签到</h3>
          <p v-if="info.checkin_closed" class="ci-notice">
            该场签到已结束，提醒已停止。原任务和本地进度仍可查看。
          </p>
          <p v-else class="ci-notice">该场签到按学习通活动截止时间提醒。</p>
          <button
            class="ci-btn"
            :disabled="store.busy || refreshingPlatform"
            @click="refreshPlatform"
          >
            {{ refreshingPlatform ? '正在刷新…' : '刷新本地记录' }}
          </button>
          <p v-if="platformRequest" class="ci-notice" role="status">{{ platformRequest }}</p>
        </section>
        <section
          v-else-if="
            info.platform_observation || info.source.source_context?.platform === 'chaoxing'
          "
          class="ci-detailsection"
          aria-label="学习通状态"
        >
          <h3>📚 学习通状态</h3>
          <p v-if="info.platform_observation">
            平台显示：{{ info.platform_observation.status || '未能读取' }} · 核对时间：{{
              displayDate(info.platform_observation.checked_at, true)
            }}
          </p>
          <p v-else>尚无可确认的唯一平台状态。</p>
          <p class="ci-notice">平台记录需确认后才更新本地任务。超过 24 小时请重新核对。</p>
          <div class="ci-actions">
            <button
              v-if="
                info.source.source_context?.platform === 'chaoxing' &&
                info.source.source_context.kind === 'task_center'
              "
              type="button"
              class="ci-btn"
              :disabled="store.busy || refreshingPlatform"
              @click="recheckPlatform"
            >
              重新核对学习通
            </button>
            <button
              class="ci-btn"
              :disabled="store.busy || refreshingPlatform"
              @click="refreshPlatform"
            >
              {{ refreshingPlatform ? '正在刷新…' : '刷新本地记录' }}
            </button>
            <button
              v-if="
                info.platform_observation?.suggested_status &&
                info.task.status !== info.platform_observation.suggested_status &&
                !['archived', 'cancelled'].includes(info.task.status)
              "
              class="ci-btn ci-primary"
              :disabled="store.busy || platformStale"
              @click="adoptPlatform"
            >
              {{
                info.platform_observation.suggested_status === 'submitted'
                  ? '确认标记已提交'
                  : '确认恢复待办'
              }}
            </button>
          </div>
          <p v-if="platformRequest" class="ci-notice" role="status">{{ platformRequest }}</p>
        </section>
        <section class="ci-detailsection" aria-label="材料清单">
          <h3>📋 材料清单</h3>
          <p class="ci-notice">勾选已准备的材料，保存后生效。</p>
          <button
            type="button"
            class="ci-btn"
            :disabled="store.busy || suggestingMaterials"
            @click="suggestMaterials"
          >
            {{ suggestingMaterials ? '正在提取…' : '从原文提取建议' }}
          </button>
          <p v-if="materialSuggestionError" class="ci-inlineerror" role="alert">
            {{ materialSuggestionError }}
          </p>
          <p
            v-else-if="materialSuggestionsChecked && !materialSuggestions.length"
            class="ci-notice"
          >
            没有找到明确的材料，请对照原文手动添加。
          </p>
          <div v-for="candidate in materialSuggestions" :key="candidate.text" class="ci-before">
            <button
              type="button"
              class="ci-btn ci-text"
              :disabled="
                store.busy ||
                materialDraft.some((item) => item.text === candidate.text) ||
                materialDraft.length >= 100
              "
              @click="materialDraft = addMaterialCandidate(materialDraft, candidate.text)"
            >
              ＋ {{ candidate.text }}
            </button>
            <small class="ci-evidence">{{ candidate.source }}：{{ candidate.quote }}</small>
          </div>
          <div v-for="(item, index) in materialDraft" :key="index" class="ci-actions">
            <label
              ><input v-model="item.done" type="checkbox" :disabled="store.busy" />
              {{ item.text }}</label
            >
            <button
              type="button"
              class="ci-btn ci-text"
              :disabled="store.busy"
              @click="materialDraft.splice(index, 1)"
            >
              移除
            </button>
          </div>
          <div class="ci-actions">
            <input
              v-model="newMaterial"
              class="ci-input"
              maxlength="200"
              placeholder="添加材料，例如实验报告"
              aria-label="材料名称"
              :disabled="store.busy"
              @keydown.enter.prevent="addMaterial"
            />
            <button
              type="button"
              class="ci-btn"
              :disabled="store.busy || !newMaterial.trim() || materialDraft.length >= 100"
              @click="addMaterial"
            >
              添加
            </button>
            <button
              type="button"
              class="ci-btn ci-primary"
              :disabled="store.busy"
              @click="saveMaterials"
            >
              保存清单
            </button>
          </div>
        </section>
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
              {{ displayMessageTime(source, true) }}</small
            >
            <p class="ci-proof">{{ source.text }}</p>
            <AttachmentText
              v-for="attachment in source.attachments"
              :key="attachment.id"
              :attachment="attachment"
            />
          </article>
        </details>
        <details v-if="info.legacy" class="ci-detailsection">
          <summary class="cursor-interaction">
            📦 旧版原始历史 · {{ info.legacy.record.history?.length || 0 }} 条
          </summary>
          <p>
            数据集：{{ info.legacy.namespace }} · 原任务编号：{{
              info.legacy.legacy_id
            }}。旧历史仅供追溯，不重放旧操作。
          </p>
          <article
            v-for="(entry, index) in info.legacy.record.history || []"
            :key="index"
            class="ci-event"
          >
            <b>{{ entry.action || '旧版操作' }}</b>
            <small>{{ entry.at || '操作时间未知' }}</small>
            <pre class="ci-proof">{{
              JSON.stringify({ before: entry.before, after: entry.after }, null, 2)
            }}</pre>
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
                    : evidence.method === 'connector'
                      ? '平台来源'
                      : '模型辅助'
              }}）</span
            >
          </p>
        </details>
        <details class="ci-detailsection">
          <summary class="cursor-interaction">🕰️ 修改历史 · {{ info.events.length }} 条</summary>
          <article v-for="event in [...info.events].reverse()" :key="event.id" class="ci-event">
            <b>{{
              event.operation === 'legacy_import'
                ? '从旧版迁入'
                : event.operation === 'legacy_source'
                  ? '保留旧版关联来源'
                  : event.operation === 'create'
                    ? '用户确认创建'
                    : event.operation === 'platform_status'
                      ? '用户确认平台状态'
                      : event.operation === 'materials'
                        ? '更新材料清单'
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
              </p>
              <p v-if="event.operation === 'platform_status' && platformEvidence(event)">
                {{ platformEvidence(event) }}
              </p></template
            >
          </article>
        </details>
      </template>
    </section>
  </div>
</template>
