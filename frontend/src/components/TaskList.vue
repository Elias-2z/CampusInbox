<script setup lang="ts">
import { courseEmoji, deadlineExpired, displayDeadline, statusNames, type Task } from '@/api'
import { useCampus } from '@/stores/campus'
import RecognitionBadge from '@/components/RecognitionBadge.vue'
defineProps<{ tasks: Task[]; canRecognize?: boolean }>()
defineEmits<{ recognize: [task: Task] }>()
const store = useCampus()
function overdue(task: Task) {
  return deadlineExpired(task, store.clock) && ['todo', 'in_progress', 'done'].includes(task.status)
}
function toggle(task: Task, event: Event) {
  const complete = ['completed', 'submitted'].includes(task.status)
  // Keep the previous state until the local task update succeeds.
  ;(event.target as HTMLInputElement).checked = complete
  void store.changeStatus(task, complete ? 'todo' : 'completed')
}
</script>
<template>
  <div v-if="tasks.length" class="ci-tasklist">
    <div
      v-for="task in tasks"
      :key="task.id"
      class="ci-task"
      :class="{ done: ['completed', 'submitted'].includes(task.status) }"
    >
      <input
        class="ci-check cursor-interaction"
        type="checkbox"
        :checked="['completed', 'submitted'].includes(task.status)"
        :disabled="store.busy || ['cancelled', 'archived'].includes(task.status)"
        :aria-label="`${['completed', 'submitted'].includes(task.status) ? '重新打开' : '完成'}：${task.title}`"
        @change="toggle(task, $event)"
      />
      <div class="ci-taskcontent">
        <RouterLink :to="`/tasks/${task.id}`" class="ci-tasktitle"
          >{{ courseEmoji(task.course) }} {{ task.title }}</RouterLink
        >
        <div class="ci-taskmeta">
          <RecognitionBadge :method="task.recognition_method || ''" :task-id="task.id" />
          <span class="ci-tag" :data-course="task.course">{{ task.course || '待分类' }}</span
          ><span :class="{ 'ci-due': overdue(task) }"
            >{{ overdue(task) ? '已逾期 · ' : '' }}{{ displayDeadline(task) }}</span
          ><span v-if="task.status !== 'todo'">{{
            task.status === 'done'
              ? '待办'
              : task.status === 'submitted'
                ? '已完成'
                : statusNames[task.status]
          }}</span>
        </div>
        <button
          v-if="canRecognize"
          type="button"
          class="ci-btn ci-text cursor-interaction"
          :disabled="store.busy"
          :aria-label="`重新识别：${task.title}`"
          @click="$emit('recognize', task)"
        >
          重新识别 ↻
        </button>
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
