<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  draftOf,
  post,
  request,
  serialize,
  type InboxItem,
  type SplitDraft,
  type Task,
} from '@/api'
import { useCampus } from '@/stores/campus'
import TaskForm from '@/components/TaskForm.vue'
import MergeReview from '@/components/MergeReview.vue'
const props = defineProps<{ item: SplitDraft }>()
const store = useCampus()
const draft = ref(draftOf(props.item.fields))
const allowDuplicate = ref(false)
const mergeMode = ref(
  props.item.matched_tasks.some((t) => ['todo', 'in_progress'].includes(t.status)),
)
const error = ref('')
watch([() => props.item.id, () => props.item.updated_at], () => {
  draft.value = draftOf(props.item.fields)
  allowDuplicate.value = false
  error.value = ''
})
async function save() {
  const result = await store.mutate(
    () =>
      request<InboxItem>(`/split-drafts/${props.item.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          ...serialize(draft.value),
          expected_updated_at: props.item.updated_at,
        }),
      }),
    '草稿已保存，尚未创建任务。',
  )
  captureError(result)
}
async function accept() {
  const result = await store.mutate(
    () =>
      post<Task>(`/split-drafts/${props.item.id}/accept`, {
        ...serialize(draft.value),
        allow_duplicate: allowDuplicate.value,
        expected_updated_at: props.item.updated_at,
      }),
    `第 ${props.item.position} 项已加入任务清单。`,
  )
  captureError(result)
}
async function decide(operation: 'ignore' | 'reopen') {
  const result = await store.mutate(
    () =>
      post<InboxItem>(`/split-drafts/${props.item.id}/${operation}`, {
        expected_updated_at: props.item.updated_at,
      }),
    operation === 'ignore' ? '已跳过此条，原文仍保留。' : '此条已恢复为待审核。',
  )
  captureError(result)
}
function captureError(result: unknown) {
  error.value = result ? '' : store.error
  store.error = ''
}
async function refreshContext() {
  await store.initialize()
  error.value = store.error
  store.error = ''
}
</script>
<template>
  <article class="ci-split-card" :aria-label="`第 ${item.position} 项草稿`">
    <h3>
      {{ item.status === 'accepted' ? '✅' : item.status === 'ignored' ? '📂' : '📝' }} 第
      {{ item.position }} 项 · {{ item.fields.title || '任务名称待补充' }}
    </h3>
    <p class="ci-proof ci-split-quote">{{ item.quote }}</p>
    <template v-if="item.status === 'accepted'">
      <p class="ci-notice ci-before">
        {{
          item.merge_change
            ? item.merge_change.reverted
              ? '本次关联与补充已撤销，原文与历史仍保留。'
              : '已关联原任务，来源和确认字段已保存。'
            : '已确认创建，重复导入不会再创建本项。'
        }}
      </p>
      <button
        v-if="item.merge_change?.can_undo"
        class="ci-btn cursor-interaction"
        :disabled="store.busy"
        @click="store.undoMerge(item.merge_change)"
      >
        撤销本次关联与补充 ↶
      </button>
      <RouterLink class="ci-btn cursor-interaction" :to="`/tasks/${item.task_id}`"
        >查看任务 →</RouterLink
      >
    </template>
    <template v-else-if="item.status === 'ignored'">
      <p class="ci-notice ci-before">此条已跳过。</p>
      <button class="ci-btn cursor-interaction" :disabled="store.busy" @click="decide('reopen')">
        恢复待审核 ↶
      </button>
    </template>
    <template v-else>
      <ul v-if="item.warnings.length" class="ci-warnings">
        <li v-for="warning in item.warnings" :key="warning">{{ warning }}</li>
      </ul>
      <div v-if="item.can_create" class="ci-actions ci-merge-mode" aria-label="条目处理方式">
        <button
          class="ci-btn cursor-interaction"
          :class="{ 'ci-primary': mergeMode }"
          :aria-pressed="mergeMode"
          :disabled="store.busy"
          @click="mergeMode = true"
        >
          🔗 关联已有任务
        </button>
        <button
          class="ci-btn cursor-interaction"
          :class="{ 'ci-primary': !mergeMode }"
          :aria-pressed="!mergeMode"
          :disabled="store.busy"
          @click="mergeMode = false"
        >
          ✨ 创建独立任务
        </button>
      </div>
      <MergeReview v-if="item.can_create && mergeMode" :source="item" split-draft />
      <form v-else-if="item.can_create" @submit.prevent="accept">
        <TaskForm v-model="draft" :busy="store.busy" :evidence="item.evidence" />
        <div v-if="item.matched_tasks.length" class="ci-match">
          <b>📎 可能重复，请先核对</b>
          <RouterLink v-for="task in item.matched_tasks" :key="task.id" :to="`/tasks/${task.id}`"
            >{{ task.course || '待分类' }} · {{ task.title }} ↗</RouterLink
          >
        </div>
        <label v-if="item.matched_tasks.length" class="ci-checklabel"
          ><input
            v-model="allowDuplicate"
            type="checkbox"
            :disabled="store.busy"
          />已核对，仍然创建独立任务</label
        >
        <div class="ci-actions">
          <button
            class="ci-btn ci-primary cursor-interaction"
            :disabled="store.busy || (!!item.matched_tasks.length && !allowDuplicate)"
          >
            确认创建第 {{ item.position }} 项 ✓
          </button>
          <button
            type="button"
            class="ci-btn cursor-interaction"
            :disabled="store.busy"
            @click="save"
          >
            保存草稿
          </button>
          <button
            type="button"
            class="ci-btn ci-text cursor-interaction"
            :disabled="store.busy"
            @click="decide('ignore')"
          >
            跳过此条
          </button>
        </div>
      </form>
      <button
        v-else
        class="ci-btn cursor-interaction"
        :disabled="store.busy"
        @click="decide('ignore')"
      >
        已核对，跳过此条
      </button>
      <button
        v-if="item.can_create && mergeMode"
        class="ci-btn ci-text cursor-interaction"
        :disabled="store.busy"
        @click="decide('ignore')"
      >
        跳过此条
      </button>
    </template>
    <div v-if="error" class="ci-inlineerror" role="alert">
      {{ error
      }}<button
        class="ci-btn ci-text cursor-interaction"
        :disabled="store.busy || store.loading"
        @click="refreshContext"
      >
        刷新草稿与关联任务 ↻
      </button>
    </div>
  </article>
</template>
