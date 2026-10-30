<script setup lang="ts">
import { courseEmoji, displayDate, statusNames, type Task } from '@/api'
import { useCampus } from '@/stores/campus'
defineProps<{ tasks: Task[] }>()
const store = useCampus()
function overdue(task: Task) {
  return (
    task.due_at &&
    new Date(task.due_at).valueOf() < store.clock &&
    ['todo', 'in_progress'].includes(task.status)
  )
}
</script>
<template>
  <div v-if="tasks.length" class="ci-tasklist">
    <div
      v-for="task in tasks"
      :key="task.id"
      class="ci-task"
      :class="{ done: task.status === 'completed' }"
    >
      <input
        class="ci-check cursor-interaction"
        type="checkbox"
        :checked="task.status === 'completed'"
        :disabled="store.busy || ['cancelled', 'archived'].includes(task.status)"
        :aria-label="`${task.status === 'completed' ? '重新打开' : '完成'}：${task.title}`"
        @change="store.changeStatus(task, task.status === 'completed' ? 'todo' : 'completed')"
      />
      <div class="ci-taskcontent">
        <RouterLink :to="`/tasks/${task.id}`" class="ci-tasktitle"
          >{{ courseEmoji(task.course) }} {{ task.title }}</RouterLink
        >
        <div class="ci-taskmeta">
          <span class="ci-tag" :data-course="task.course">{{ task.course || '待分类' }}</span
          ><span :class="{ 'ci-due': overdue(task) }"
            >{{ overdue(task) ? '已逾期 · ' : '' }}{{ displayDate(task.due_at) }}</span
          ><span v-if="task.status !== 'todo'">{{ statusNames[task.status] }}</span>
        </div>
      </div>
      <RouterLink
        class="ci-arrow cursor-interaction"
        :to="`/tasks/${task.id}`"
        :aria-label="`查看${task.title}详情`"
        >↗</RouterLink
      >
    </div>
  </div>
  <div v-else class="ci-empty">
    <span>🌱</span>
    <p>这里暂时没有任务，给今天留一点余地。</p>
  </div>
</template>
