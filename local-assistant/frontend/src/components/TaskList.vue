<script setup lang="ts">
import { formatDeadline, overdue, statusNames, type Task, type TaskStatus } from '../domain'
defineProps<{ tasks: Task[]; now: Date }>()
defineEmits<{ open: [task: Task]; status: [task: Task, status: TaskStatus] }>()
</script>
<template>
  <div class="ci-tasklist">
    <article
      v-for="task in tasks"
      :key="task.id"
      class="ci-task"
      :class="{ 'ci-task-done': task.status === 'submitted' }"
    >
      <button
        class="ci-taskcheck"
        :aria-label="task.status === 'done' ? '确认已提交 ' + task.title : '标记做完 ' + task.title"
        :disabled="['submitted', 'cancelled'].includes(task.status)"
        @click="$emit('status', task, task.status === 'done' ? 'submitted' : 'done')"
      >
        {{ task.status === 'submitted' ? '✓' : task.status === 'done' ? '↥' : '○' }}
      </button>
      <div class="ci-taskbody">
        <button class="ci-tasktitle" @click="$emit('open', task)">{{ task.title }}</button>
        <div class="ci-taskmeta">
          <span v-if="task.sample" class="ci-sample">示例</span>
          <span class="ci-tag">{{ task.course || '课程待填写' }}</span
          ><span v-if="task.scope">{{ task.scope }}</span
          ><span>{{ statusNames[task.status] }}</span
          ><span v-if="task.checklist?.length"
            >材料 {{ task.checklist.filter((x) => x.done).length }}/{{
              task.checklist.length
            }}</span
          >
        </div>
        <div v-if="task.platform_check?.status" class="ci-muted ci-small">
          学习通：{{ task.platform_check.status
          }}<span
            v-if="
              task.platform_check.suggested_status &&
              task.platform_check.suggested_status !== task.status
            "
          >
            · 需核对本地状态</span
          >
        </div>
        <div
          v-if="task.checkin_verification && task.checkin_verification !== 'verified'"
          class="ci-muted ci-small"
        >
          签到状态待核验，请查看完整详情
        </div>
      </div>
      <button
        class="ci-deadline"
        :class="{ 'ci-overdue': overdue(task, now) }"
        @click="$emit('open', task)"
      >
        {{ formatDeadline(task.deadline) }}<small v-if="overdue(task, now)">已逾期</small>
      </button>
    </article>
  </div>
</template>
