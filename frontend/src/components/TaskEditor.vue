<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { draftOf, post, request, serialize, statusNames, type Task, type TaskStatus } from '@/api'
import { useCampus } from '@/stores/campus'
import AppModal from './AppModal.vue'
import TaskForm from './TaskForm.vue'
const store = useCampus()
const router = useRouter()
const task = store.editorTask
const draft = ref(draftOf(task || {}))
const status = ref<TaskStatus>(task?.status || 'todo')
async function submit() {
  const result = await store.mutate(
    () =>
      task
        ? request<Task>(`/tasks/${task.id}`, {
            method: 'PATCH',
            body: JSON.stringify({
              ...serialize(draft.value),
              status: status.value,
              expected_version: task.version,
            }),
          })
        : post<Task>('/tasks', serialize(draft.value)),
    task ? '任务修改已保存，历史记录已保留。' : '新任务已加入清单。',
  )
  if (result) {
    store.editorOpen = false
    await router.push(`/tasks/${result.id}`)
  }
}
</script>
<template>
  <AppModal
    :title="task ? '✏️ 编辑任务' : '✨ 记下一件事'"
    :locked="store.busy"
    @close="store.editorOpen = false"
  >
    <form @submit.prevent="submit">
      <TaskForm v-model="draft" :busy="store.busy" />
      <label v-if="task" class="ci-field"
        >状态<select v-model="status" class="ci-input" :disabled="store.busy">
          <option v-for="(name, key) in statusNames" :key="key" :value="key">{{ name }}</option>
        </select></label
      >
      <div v-if="store.error" class="ci-inlineerror" role="alert">{{ store.error }}</div>
      <div class="ci-actions">
        <button class="ci-btn ci-primary cursor-interaction" :disabled="store.busy">
          {{ store.busy ? '正在保存…' : task ? '保存修改 ✓' : '创建任务 ✓' }}
        </button>
      </div>
    </form>
  </AppModal>
</template>
