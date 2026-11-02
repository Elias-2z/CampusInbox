<script setup lang="ts">
import { computed, ref } from 'vue'
import { calendarDays, dayOf, formatDeadline, type Task } from '../domain'
import { download } from '../api'
const props = defineProps<{ tasks: Task[]; now: Date }>()
const emit = defineEmits<{ open: [task: Task]; error: [message: string] }>()
const selected = ref(dayOf(props.now))
const mode = ref<'month' | 'week'>('month')
const course = ref('')
const status = ref('active')
const courses = computed(() => [...new Set(props.tasks.map((t) => t.course).filter(Boolean))])
const days = computed(() => calendarDays(selected.value, mode.value))
const filtered = computed(() =>
  props.tasks.filter(
    (t) =>
      (!course.value || t.course === course.value) &&
      (status.value === 'all' ||
        (status.value === 'active'
          ? !['submitted', 'cancelled'].includes(t.status)
          : t.status === status.value)),
  ),
)
const selectedTasks = computed(() =>
  filtered.value.filter((t) => dayOf(t.deadline) === selected.value),
)
const undated = computed(() => filtered.value.filter((t) => !t.deadline))
function shift(direction: number) {
  const d = new Date(selected.value + 'T12:00:00Z')
  if (mode.value === 'month') {
    d.setUTCDate(1)
    d.setUTCMonth(d.getUTCMonth() + direction)
  } else d.setUTCDate(d.getUTCDate() + 7 * direction)
  selected.value = d.toISOString().slice(0, 10)
}
async function exportCalendar() {
  try {
    await download('/calendar.ics', 'campusinbox.ics')
  } catch (e) {
    emit('error', e instanceof Error ? e.message : '导出失败')
  }
}
</script>
<template>
  <div class="ci-calendar-tools">
    <div class="ci-actions">
      <button class="ci-btn" @click="shift(-1)" aria-label="上一个日期范围">←</button
      ><strong>{{ selected.slice(0, 7).replace('-', ' 年 ') }} 月</strong
      ><button class="ci-btn" @click="shift(1)" aria-label="下一个日期范围">→</button
      ><button class="ci-btn" @click="selected = dayOf(now)">今天</button>
    </div>
    <div class="ci-actions">
      <button class="ci-btn" :class="{ 'ci-primary': mode === 'month' }" @click="mode = 'month'">
        月</button
      ><button class="ci-btn" :class="{ 'ci-primary': mode === 'week' }" @click="mode = 'week'">
        周</button
      ><button class="ci-btn" @click="exportCalendar">导出全部日历 .ics</button>
    </div>
  </div>
  <div class="ci-filters">
    <select v-model="course" class="ci-input" aria-label="日历课程">
      <option value="">全部课程</option>
      <option v-for="c in courses" :key="c">{{ c }}</option></select
    ><select v-model="status" class="ci-input" aria-label="日历状态">
      <option value="active">尚未提交</option>
      <option value="all">全部状态</option>
      <option value="submitted">已提交</option>
      <option value="done">已做完</option>
    </select>
  </div>
  <div class="ci-calendar-grid">
    <div v-for="day in ['一', '二', '三', '四', '五', '六', '日']" :key="day" class="ci-weeklabel">
      周{{ day }}
    </div>
    <button
      v-for="day in days"
      :key="day"
      class="ci-calendar-day"
      :class="{
        selected: day === selected,
        today: day === dayOf(now),
        outside: day.slice(0, 7) !== selected.slice(0, 7),
      }"
      @click="selected = day"
    >
      <span>{{ Number(day.slice(-2)) }}</span
      ><span
        v-for="task in filtered.filter((t) => dayOf(t.deadline) === day).slice(0, 2)"
        :key="task.id"
        class="ci-calendar-event"
        >{{ task.title }}</span
      ><small v-if="filtered.filter((t) => dayOf(t.deadline) === day).length"
        >{{ filtered.filter((t) => dayOf(t.deadline) === day).length }} 项</small
      >
    </button>
  </div>
  <section class="ci-calendar-agenda">
    <h2>
      {{ selected }} 的安排 <span class="ci-count">{{ selectedTasks.length }} 项</span>
    </h2>
    <button
      v-for="task in selectedTasks"
      :key="task.id"
      class="ci-agenda-item"
      @click="emit('open', task)"
    >
      <strong>{{ task.title }}</strong
      ><span>{{ task.course }} · {{ formatDeadline(task.deadline) }}</span>
    </button>
    <p v-if="!selectedTasks.length" class="ci-muted">这一天没有符合筛选的任务。</p>
  </section>
  <section v-if="undated.length" class="ci-calendar-agenda">
    <h2>
      截止日期待补充 <span class="ci-count">{{ undated.length }} 项</span>
    </h2>
    <button
      v-for="task in undated"
      :key="task.id"
      class="ci-agenda-item"
      @click="emit('open', task)"
    >
      {{ task.title }} <span>{{ task.course }}</span>
    </button>
  </section>
</template>
