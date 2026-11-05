<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  displayMessageTime,
  displayDeadline,
  draftOf,
  post,
  serialize,
  type InboxItem,
  type Task,
  type TaskInput,
} from '@/api'
import { useCampus } from '@/stores/campus'
import TaskForm from '@/components/TaskForm.vue'
import AttachmentText from '@/components/AttachmentText.vue'
import DeadlineReview from '@/components/DeadlineReview.vue'
import CancellationReview from '@/components/CancellationReview.vue'
import SplitReview from '@/components/SplitReview.vue'
import MergeReview from '@/components/MergeReview.vue'
const store = useCampus()
const route = useRoute()
const router = useRouter()
const tab = ref('pending')
const selectedId = ref('')
const drafts = reactive<Record<string, TaskInput>>({})
const duplicateAllowed = ref(false)
const mergeMode = ref(false)
const tabs = [
  { id: 'pending', title: '待处理' },
  { id: 'accepted', title: '已处理' },
  { id: 'ignored', title: '已忽略' },
  { id: 'archived', title: '历史归档' },
  { id: 'all', title: '全部' },
]
const items = computed(() =>
  store.inbox.filter(
    (i) =>
      tab.value === 'all' ||
      i.status === tab.value ||
      (tab.value === 'pending' && i.status === 'processing'),
  ),
)
const selected = computed(
  () => items.value.find((i) => i.id === selectedId.value) || items.value[0],
)
const isCheckin = computed(() => {
  const context = selected.value?.message.source_context
  return context?.platform === 'chaoxing' && context.kind === 'checkin_activity'
})
const draft = computed({
  get: () => {
    const i = selected.value
    if (!i) return draftOf()
    return drafts[i.id] || draftOf(i.fields)
  },
  set: (value) => {
    if (selected.value) drafts[selected.value.id] = value
  },
})
watch(
  () => [route.query.id, store.ready],
  () => {
    const item = store.inbox.find((i) => i.id === route.query.id)
    if (item) {
      selectedId.value = item.id
      tab.value = item.status === 'processing' ? 'pending' : item.status
    }
  },
  { immediate: true },
)
watch(
  () => store.inbox.find((i) => i.id === selectedId.value)?.status,
  (status) => {
    const item = store.inbox.find((i) => i.id === selectedId.value)
    if (status && item?.split_items.length) tab.value = status
  },
)
watch(
  [() => selected.value?.id, () => selected.value?.status],
  ([id, status], previous) => {
    if (id && !selectedId.value) selectedId.value = String(id)
    duplicateAllowed.value = false
    mergeMode.value = !!selected.value?.matched_tasks.some((t) =>
      ['todo', 'in_progress'].includes(t.status),
    )
    if (
      id &&
      selected.value &&
      (!drafts[String(id)] || (status === 'pending' && previous?.[1] === 'processing'))
    )
      drafts[String(id)] = draftOf(selected.value.fields)
  },
  { immediate: true },
)
async function selectTab(value: string) {
  tab.value = value
  selectedId.value = ''
  await router.replace('/inbox')
}
async function select(item: InboxItem) {
  selectedId.value = item.id
  await router.replace({ query: { id: item.id } })
}
async function accept() {
  if (!selected.value) return
  const item = selected.value
  const task = await store.mutate(
    () =>
      post<Task>(`/suggestions/${item.id}/accept`, {
        ...serialize(draft.value),
        allow_duplicate: duplicateAllowed.value,
      }),
    '新任务已加入清单，原始通知也保存好了。',
  )
  if (task) await router.push(`/tasks/${task.id}`)
}
async function retry() {
  const item = selected.value
  if (!item || store.busy) return
  if (
    JSON.stringify(draft.value) !== JSON.stringify(draftOf(item.fields)) &&
    !window.confirm('重新识别会替换当前草稿中尚未保存的修改。确定继续？')
  ) {
    return
  }
  const id = item.id
  const result = await store.mutate(
    () => post<InboxItem>(`/inbox/${id}/analyze`),
    '重新识别完成，请核对结果。',
  )
  if (result) drafts[id] = draftOf(result.fields)
}
async function fetchOriginal(eventId: string | undefined, index: number) {
  if (!eventId) return
  await store.mutate(
    () =>
      post(`/connectors/chaoxing/events/${encodeURIComponent(eventId)}/attachments/${index}/fetch`),
    '原件已保存，可查看附件或重新识别。',
  )
}
function processedLabel(item: InboxItem) {
  if (item.split_items.length) return `已处理 ${item.split_items.length} 项条目`
  if (item.merge_change) return item.merge_change.reverted ? '关联已撤销' : '已关联原任务'
  if (item.cancellation_change)
    return item.cancellation_change.reverted ? '取消已撤销' : '已取消原任务'
  if (item.deadline_change) return item.deadline_change.reverted ? '更新已撤销' : '已更新原任务'
  return '已创建任务'
}
function processedDescription(item: InboxItem) {
  if (item.merge_change)
    return item.merge_change.reverted
      ? '本次关联与字段补充已撤销，通知和操作历史仍保留。'
      : '本条通知已关联原任务，并应用明确勾选的字段。'
  if (item.cancellation_change)
    return item.cancellation_change.reverted
      ? '本次取消已撤销，通知和操作历史仍保留。'
      : '取消通知已关联原任务，任务内容仍保留。'
  if (item.deadline_change)
    return item.deadline_change.reverted
      ? '本次时间更新已撤销，通知和操作历史仍保留。'
      : '这条变更已应用到原任务，来源已保存。'
  return '这条通知已经处理，重复导入不会再次创建任务。'
}
const methodName = (method: string) =>
  ({ rule: '规则识别', mixed: '规则 + 模型辅助', manual: '手动创建', legacy: '旧版迁移，待核对' })[
    method
  ] || method
</script>
<template>
  <div class="ci-pagehead">
    <div>
      <div class="ci-eyebrow">A LITTLE SORTING, A LOT OF CLARITY</div>
      <h1>你的信息中转站 📮</h1>
    </div>
  </div>
  <div class="ci-filters" aria-label="收件箱状态">
    <button
      v-for="t in tabs"
      :key="t.id"
      class="ci-filter cursor-interaction"
      :aria-pressed="tab === t.id"
      @click="selectTab(t.id)"
    >
      {{ t.title }}<span v-if="t.id === 'pending'"> · {{ store.pending.length }}</span>
    </button>
  </div>
  <div v-if="store.ready && !items.length" class="ci-first">
    <span class="ci-firstemoji">{{ tab === 'pending' ? '🎉' : '📭' }}</span>
    <h2>{{ tab === 'pending' ? '暂时没有待处理通知' : '这里还没有记录' }}</h2>
    <p>这里集中审核识别结果。需要添加内容时，请到“我的任务”中选择“新建任务”。</p>
    <RouterLink class="ci-btn ci-primary cursor-interaction" to="/tasks">前往我的任务 →</RouterLink>
  </div>
  <div v-else-if="selected" class="ci-inboxlayout">
    <aside class="ci-messagelist" aria-label="通知列表">
      <button
        v-for="item in items"
        :key="item.id"
        class="ci-message cursor-interaction"
        :aria-pressed="selected.id === item.id"
        @click="select(item)"
      >
        <b
          >{{
            item.split_supported
              ? '🧩'
              : item.action === 'review'
                ? '🗓️'
                : item.action === 'ignore'
                  ? '💬'
                  : '✨'
          }}
          {{
            item.split_supported
              ? '多任务通知'
              : item.fields.title ||
                item.message.text.slice(0, 28) ||
                (item.message.source_context?.platform === 'chaoxing'
                  ? item.message.source_context.title
                  : '') ||
                '原始通知'
          }}</b
        ><small>{{ item.message.source }} · {{ displayMessageTime(item.message) }}</small
        ><span>{{
          item.status === 'processing'
            ? '正在识别'
            : item.status === 'accepted'
              ? processedLabel(item)
              : item.status === 'ignored'
                ? '已忽略'
                : item.status === 'archived'
                  ? '历史归档'
                  : item.split_items.length
                    ? `已确认 ${item.split_items.filter((d) => d.status === 'accepted').length} / 共 ${item.split_items.length} 项`
                    : item.split_supported
                      ? '多任务待拆分'
                      : item.action === 'review'
                        ? '变更待核对'
                        : item.action === 'ignore'
                          ? '可能无需创建任务'
                          : '等待你确认'
        }}</span>
      </button>
    </aside>
    <div class="ci-review">
      <section class="ci-source">
        <div class="ci-sourcehead">📌 原始通知</div>
        <p class="ci-quote">{{ selected.message.text || '原通知正文为空。' }}</p>
        <div v-if="selected.activity_schedule?.length" class="ci-notice">
          <strong>原文中的实验安排</strong>
          <ul>
            <li v-for="quote in selected.activity_schedule" :key="quote">{{ quote }}</li>
          </ul>
        </div>
        <AttachmentText
          v-for="attachment in selected.message.attachments"
          :key="attachment.id"
          :attachment="attachment"
        >
          <template #actions>
            <button
              v-if="selected.status === 'pending' && !selected.split_items.length && !isCheckin"
              type="button"
              class="ci-btn ci-text cursor-interaction"
              :disabled="store.busy"
              @click="retry"
            >
              结合附件重新识别
            </button>
          </template>
        </AttachmentText>
        <div
          v-if="
            selected.message.source_context?.platform === 'chaoxing' &&
            selected.message.source_context.event_id &&
            selected.message.source_context.missing_originals?.length
          "
        >
          <button
            v-for="original in selected.message.source_context.missing_originals"
            :key="original.index"
            class="ci-btn cursor-interaction"
            :disabled="store.busy"
            @click="fetchOriginal(selected.message.source_context.event_id, original.index)"
          >
            {{ original.recoverable ? '恢复原件' : '补下载原件' }} · {{ original.name }}
          </button>
        </div>
        <div class="ci-sourcefoot">
          {{ selected.message.source }}
          <details v-if="selected.message.source_context?.platform === 'qq'">
            <summary>查看群与发件人详情</summary>
            <p>
              原群名：{{ selected.message.source_context.group_name }}<br />群号：{{
                selected.message.source_context.group_id
              }}<br />发件人 QQ：{{ selected.message.source_context.sender_id }}
            </p>
          </details>
          <details v-else-if="selected.message.source_context?.platform === 'chaoxing'">
            <summary>查看学习通来源详情</summary>
            <p>
              <template v-if="selected.message.source_context.course"
                >课程：{{ selected.message.source_context.course }}<br
              /></template>
              <template v-if="selected.message.source_context.group_name"
                >会话：{{ selected.message.source_context.group_name }}<br
              /></template>
              <template v-if="selected.message.source_context.sender_name"
                >发件人：{{ selected.message.source_context.sender_name }}<br
              /></template>
              <template v-if="selected.message.source_context.message_id"
                >原消息编号：{{ selected.message.source_context.message_id }}</template
              >
            </p>
          </details>
          <details v-else-if="selected.message.source_context?.platform === 'legacy'">
            <summary>旧版来源</summary>
            <p>
              数据集：{{ selected.message.source_context.namespace }} · 原消息编号：{{
                selected.message.source_context.legacy_id
              }}
            </p>
          </details>
          <br />发送于 {{ displayMessageTime(selected.message, true) }}<br />消息时区：{{
            selected.message.timezone
          }}<br />{{ methodName(selected.method) }}
        </div>
        <div
          v-if="
            Object.keys(selected.evidence).length &&
            !selected.split_supported &&
            !selected.split_items.length
          "
          class="ci-sourceevidence"
        >
          <h3>识别依据</h3>
          <p v-for="(value, key) in selected.evidence" :key="key">
            <b>{{
              key === 'due_at' || key === 'due_date'
                ? '截止时间'
                : key === 'title'
                  ? '任务名称'
                  : key === 'course'
                    ? '课程'
                    : key === 'file_requirements'
                      ? '文件要求'
                      : key === 'submission_platform'
                        ? '提交平台'
                        : '其他字段'
            }}</b>
            · {{ value.quote }}
          </p>
        </div>
      </section>
      <section class="ci-form">
        <template v-if="selected.status === 'processing'"
          ><h3>正在整理这条通知…</h3>
          <p class="ci-notice">原文已经保存。稍后刷新即可查看建议。</p>
          <button
            class="ci-btn cursor-interaction"
            :disabled="store.loading"
            @click="store.initialize"
          >
            刷新结果
          </button></template
        >
        <SplitReview
          v-else-if="selected.split_supported || selected.split_items.length"
          :key="selected.id"
          :item="selected"
        />
        <template v-else-if="selected.status === 'accepted'"
          ><h3>✅ {{ processedLabel(selected) }}</h3>
          <p class="ci-notice ci-before">{{ processedDescription(selected) }}</p>
          <button
            v-if="selected.merge_change?.can_undo"
            class="ci-btn cursor-interaction"
            :disabled="store.busy"
            @click="store.undoMerge(selected.merge_change)"
          >
            撤销本次关联与补充 ↶
          </button>
          <button
            v-if="selected.cancellation_change?.can_undo"
            class="ci-btn cursor-interaction"
            :disabled="store.busy"
            @click="store.undoCancellation(selected.cancellation_change)"
          >
            撤销本次取消 ↶
          </button>
          <button
            v-if="selected.deadline_change?.can_undo"
            class="ci-btn cursor-interaction"
            :disabled="store.busy"
            @click="store.undoDeadline(selected.deadline_change)"
          >
            撤销本次截止时间更新 ↶
          </button>
          <RouterLink
            v-if="selected.task_id"
            class="ci-btn ci-primary cursor-interaction"
            :to="`/tasks/${selected.task_id}`"
            >查看任务 →</RouterLink
          ></template
        >
        <template v-else-if="selected.status === 'archived'">
          <h3>📂 历史归档，原文仍在</h3>
          <p v-if="!selected.warnings.length" class="ci-notice">请核对后决定是否恢复处理。</p>
          <ul v-if="selected.warnings.length" class="ci-warnings">
            <li v-for="warning in selected.warnings" :key="warning">{{ warning }}</li>
          </ul>
          <button
            v-if="
              selected.message.source_context?.platform !== 'chaoxing' ||
              !selected.message.source_context.closed_task_status
            "
            class="ci-btn cursor-interaction"
            :disabled="store.busy"
            @click="
              store.mutate(
                () => post(`/suggestions/${selected!.id}/restore-archive`),
                '已恢复到待处理。',
              )
            "
          >
            恢复为待处理
          </button>
        </template>
        <template v-else-if="selected.status === 'ignored'"
          ><h3>📂 已忽略，原文仍在</h3>
          <button
            class="ci-btn cursor-interaction"
            :disabled="store.busy"
            @click="
              store.mutate(() => post(`/suggestions/${selected!.id}/reopen`), '已恢复到待处理。')
            "
          >
            恢复为待处理
          </button></template
        >
        <template v-else
          ><h3>
            {{
              selected.action === 'review'
                ? '🗓️ 这条变化需要你核对'
                : selected.action === 'ignore'
                  ? '💬 可能不需要创建任务'
                  : '✨ 帮你整理好了'
            }}
          </h3>
          <ul v-if="selected.warnings.length" class="ci-warnings">
            <li v-for="warning in selected.warnings" :key="warning">{{ warning }}</li>
          </ul>
          <div v-if="selected.matched_tasks.length" class="ci-match">
            <b>📎 可能关联已有任务</b
            ><RouterLink
              v-for="task in selected.matched_tasks"
              :key="task.id"
              :to="`/tasks/${task.id}`"
              >{{ task.title }} · {{ displayDeadline(task) }} ↗</RouterLink
            >
          </div>
          <CancellationReview
            v-if="selected.cancellation_supported"
            :key="selected.id"
            :item="selected"
          />
          <DeadlineReview
            v-else-if="selected.deadline_supported"
            :key="selected.id"
            :item="selected"
          />
          <template v-else-if="selected.action === 'create'">
            <div
              v-if="
                mergeMode ||
                store.tasks.some((task) => ['todo', 'in_progress', 'done'].includes(task.status))
              "
              class="ci-review-mode"
            >
              <button
                type="button"
                class="ci-btn ci-text cursor-interaction"
                :disabled="store.busy"
                @click="mergeMode = !mergeMode"
              >
                {{ mergeMode ? '← 返回新建任务' : '🔗 关联已有任务' }}
              </button>
            </div>
            <MergeReview v-if="mergeMode" :source="selected" />
            <form v-else @submit.prevent="accept">
              <TaskForm v-model="draft" :busy="store.busy" :evidence="selected.evidence" /><label
                v-if="selected.matched_tasks.length"
                class="ci-checklabel"
                ><input
                  v-model="duplicateAllowed"
                  type="checkbox"
                  :disabled="store.busy"
                />已核对，仍然创建独立任务</label
              >
              <div class="ci-actions">
                <button
                  class="ci-btn ci-primary cursor-interaction"
                  :disabled="store.busy || (!!selected.matched_tasks.length && !duplicateAllowed)"
                >
                  {{ store.busy ? '正在保存…' : '确认创建任务 ✓' }}</button
                ><button
                  type="button"
                  class="ci-btn ci-text cursor-interaction"
                  :disabled="store.busy"
                  @click="store.ignore(selected)"
                >
                  忽略这条
                </button>
              </div>
            </form>
          </template>
          <div
            v-else-if="!selected.deadline_supported && !selected.cancellation_supported"
            class="ci-actions"
          >
            <button
              class="ci-btn cursor-interaction"
              :disabled="store.busy"
              @click="store.ignore(selected)"
            >
              忽略这条建议</button
            ><RouterLink class="ci-btn ci-text cursor-interaction" to="/tasks">
              前往我的任务
            </RouterLink>
          </div>
          <div class="ci-actions ci-review-footer">
            <button
              v-if="selected.deadline_supported || selected.cancellation_supported"
              class="ci-btn ci-text cursor-interaction"
              :disabled="store.busy"
              @click="store.ignore(selected)"
            >
              忽略这条建议
            </button>
            <button
              v-if="!isCheckin"
              class="ci-btn ci-text ci-retry cursor-interaction"
              :disabled="store.busy"
              @click="retry"
            >
              重新识别 ↻
            </button>
          </div>
        </template>
      </section>
    </div>
  </div>
</template>
