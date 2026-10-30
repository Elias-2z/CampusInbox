<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  displayDate,
  draftOf,
  post,
  serialize,
  type InboxItem,
  type Task,
  type TaskInput,
} from '@/api'
import { useCampus } from '@/stores/campus'
import TaskForm from '@/components/TaskForm.vue'
import DeadlineReview from '@/components/DeadlineReview.vue'
const store = useCampus()
const route = useRoute()
const router = useRouter()
const tab = ref('pending')
const selectedId = ref('')
const drafts = reactive<Record<string, TaskInput>>({})
const duplicateAllowed = ref(false)
const tabs = [
  { id: 'pending', title: '待处理' },
  { id: 'accepted', title: '已处理' },
  { id: 'ignored', title: '已忽略' },
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
  () => [selected.value?.id, selected.value?.status],
  ([id, status], previous) => {
    duplicateAllowed.value = false
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
  if (!selected.value) return
  const id = selected.value.id
  const result = await store.mutate(
    () => post<InboxItem>(`/inbox/${id}/analyze`),
    '重新识别完成，请核对结果。',
  )
  if (result) drafts[id] = draftOf(result.fields)
}
const methodName = (method: string) =>
  ({ rule: '规则识别', mixed: '规则 + 模型辅助', manual: '手动创建' })[method] || method
</script>
<template>
  <div class="ci-pagehead">
    <div>
      <div class="ci-eyebrow">A LITTLE SORTING, A LOT OF CLARITY</div>
      <h1>你的信息中转站 📮</h1>
    </div>
    <button
      class="ci-btn ci-primary cursor-interaction"
      :disabled="!store.ready"
      @click="store.importing = true"
    >
      ＋ 导入通知
    </button>
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
    <p>导入一条通知，原文会先保存在这里。</p>
    <button class="ci-btn ci-primary cursor-interaction" @click="store.importing = true">
      导入通知 →
    </button>
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
          >{{ item.action === 'review' ? '🗓️' : item.action === 'ignore' ? '💬' : '✨' }}
          {{ item.fields.title || item.message.text.slice(0, 28) }}</b
        ><small>{{ item.message.source }} · {{ displayDate(item.message.sent_at) }}</small
        ><span>{{
          item.status === 'processing'
            ? '正在识别'
            : item.status === 'accepted'
              ? item.deadline_change
                ? item.deadline_change.reverted
                  ? '更新已撤销'
                  : '已更新原任务'
                : '已创建任务'
              : item.status === 'ignored'
                ? '已忽略'
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
        <p class="ci-quote">{{ selected.message.text }}</p>
        <div class="ci-sourcefoot">
          {{ selected.message.source }}<br />发送于 {{ displayDate(selected.message.sent_at, true)
          }}<br />消息时区：{{ selected.message.timezone }}<br />{{ methodName(selected.method) }}
        </div>
        <div v-if="Object.keys(selected.evidence).length" class="ci-sourceevidence">
          <h3>识别依据</h3>
          <p v-for="(value, key) in selected.evidence" :key="key">
            <b>{{
              key === 'due_at'
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
        <template v-else-if="selected.status === 'accepted'"
          ><h3>
            {{
              selected.deadline_change
                ? selected.deadline_change.reverted
                  ? '↶ 本次更新已撤销'
                  : '✅ 已更新原任务'
                : '✅ 已创建正式任务'
            }}
          </h3>
          <p class="ci-notice ci-before">
            {{
              selected.deadline_change
                ? selected.deadline_change.reverted
                  ? '原截止时间已恢复，通知与修改历史仍然保留。'
                  : '这条变更已应用到原任务，没有创建重复任务。'
                : '这条通知已经处理，重复导入不会再次创建任务。'
            }}
          </p>
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
              >{{ task.title }} · {{ displayDate(task.due_at) }} ↗</RouterLink
            >
          </div>
          <DeadlineReview v-if="selected.deadline_supported" :key="selected.id" :item="selected" />
          <form v-else-if="selected.action === 'create'" @submit.prevent="accept">
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
          <div v-else-if="!selected.deadline_supported" class="ci-actions">
            <button
              class="ci-btn cursor-interaction"
              :disabled="store.busy"
              @click="store.ignore(selected)"
            >
              忽略这条建议</button
            ><button class="ci-btn ci-text cursor-interaction" @click="store.edit()">
              手动新建任务
            </button>
          </div>
          <div class="ci-actions ci-review-footer">
            <button
              v-if="selected.deadline_supported"
              class="ci-btn ci-text cursor-interaction"
              :disabled="store.busy"
              @click="store.ignore(selected)"
            >
              忽略这条建议
            </button>
            <button
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
