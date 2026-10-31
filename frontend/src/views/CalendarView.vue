<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  courseEmoji,
  downloadCalendar,
  request,
  statusNames,
  type CalendarFeed,
  type Task,
} from '@/api'
import { addDays, dayKey, days, parseDay, range, shiftMonth } from '@/calendar'
import { useCampus } from '@/stores/campus'
import TaskList from '@/components/TaskList.vue'
const route = useRoute()
const router = useRouter()
const store = useCampus()
const scopes = [
  { id: 'active', label: '待办与进行中' },
  { id: 'all', label: '全部（不含归档）' },
  { id: 'todo', label: '待办' },
  { id: 'in_progress', label: '进行中' },
  { id: 'completed', label: '已完成' },
  { id: 'cancelled', label: '已取消' },
  { id: 'archived', label: '已归档' },
]
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai'
const view = computed(() => (route.query.view === 'week' ? 'week' : 'month'))
const anchor = computed(
  () => parseDay(route.query.date) || parseDay(dayKey(new Date(store.clock)))!,
)
const scope = computed(() =>
  scopes.some((s) => s.id === route.query.scope) ? String(route.query.scope) : 'active',
)
const course = computed(() =>
  typeof route.query.course === 'string' && route.query.course.length <= 100
    ? route.query.course
    : '',
)
const windowRange = computed(() => range(anchor.value, view.value))
const visibleDays = computed(() => days(anchor.value, view.value))
const selected = computed(() => dayKey(anchor.value))
const feed = ref<CalendarFeed | null>(null)
const loading = ref(false)
const error = ref('')
const exporting = ref(false)
const exportMessage = ref('')
let revision = 0
const courses = computed(() =>
  [
    ...new Set(
      [...store.tasks, ...(feed.value?.tasks || []), ...(feed.value?.undated || [])]
        .map((task) => task.course)
        .filter(Boolean),
    ),
  ].sort(),
)
const grouped = computed(() => {
  const result: Record<string, Task[]> = {}
  for (const task of feed.value?.tasks || []) {
    const key = dayKey(new Date(task.due_at!))
    ;(result[key] ||= []).push(task)
  }
  return result
})
const selectedTasks = computed(() => grouped.value[selected.value] || [])
const heading = computed(() =>
  view.value === 'month'
    ? anchor.value.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long' })
    : `${windowRange.value.start} — ${dayKey(visibleDays.value[6]!)}`,
)
function query() {
  return new URLSearchParams({
    ...windowRange.value,
    timezone,
    status: scope.value,
    course: course.value,
  })
}
function update(patch: Record<string, string>) {
  return router.replace({
    path: '/calendar',
    query: {
      view: view.value,
      date: selected.value,
      scope: scope.value,
      ...(course.value ? { course: course.value } : {}),
      ...patch,
    },
  })
}
function move(count: number) {
  const value =
    view.value === 'month' ? shiftMonth(anchor.value, count) : addDays(anchor.value, count * 7)
  if (parseDay(dayKey(value))) void update({ date: dayKey(value) })
}
function chooseDate(event: Event) {
  const value = (event.target as HTMLInputElement).value
  if (parseDay(value)) void update({ date: value })
}
function filter(event: Event, key: string) {
  void update({ [key]: (event.target as HTMLSelectElement).value })
}
function inMonth(day: Date) {
  return view.value === 'week' || day.getMonth() === anchor.value.getMonth()
}
function label(day: Date) {
  if (!inMonth(day)) return `${dayKey(day)}，查看该月任务`
  const count = grouped.value[dayKey(day)]?.length || 0
  return `${dayKey(day)}，${count} 项截止任务`
}
function clock(value: string) {
  return new Date(value).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}
async function load() {
  if (!store.ready) return
  const version = ++revision
  const params = query()
  loading.value = true
  feed.value = null
  error.value = ''
  exportMessage.value = ''
  try {
    const result = await request<CalendarFeed>(`/calendar?${params}`)
    if (version === revision) feed.value = result
  } catch (e) {
    if (version === revision) error.value = store.errorMessage(e)
  } finally {
    if (version === revision) loading.value = false
  }
}
function refreshVisible() {
  if (document.visibilityState === 'visible') void load()
}
watch(
  [
    () => windowRange.value.start,
    () => windowRange.value.end,
    scope,
    course,
    () => store.ready,
    () => store.tasks,
  ],
  load,
  { immediate: true },
)
onMounted(() => {
  document.addEventListener('visibilitychange', refreshVisible)
  window.addEventListener('focus', refreshVisible)
})
onUnmounted(() => {
  revision++
  document.removeEventListener('visibilitychange', refreshVisible)
  window.removeEventListener('focus', refreshVisible)
})
async function exportIcs() {
  if (exporting.value || !feed.value?.tasks.length) return
  exporting.value = true
  error.value = ''
  exportMessage.value = ''
  const params = query()
  const label = `${heading.value} · ${course.value || '全部课程'} · ${scopes.find((s) => s.id === scope.value)?.label}`
  try {
    await downloadCalendar(params)
    exportMessage.value = `${label} 的日历文件已生成，请查看下载。文件是导出时的数据快照；后续变化需要重新导出。`
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    exporting.value = false
  }
}
</script>
<template>
  <div class="ci-pagehead">
    <div>
      <div class="ci-eyebrow">MAKE ROOM FOR WHAT MATTERS</div>
      <h1>把日子，安排得明明白白 🗓️</h1>
    </div>
    <div class="ci-actions">
      <button
        class="ci-btn cursor-interaction"
        :disabled="store.loading || loading"
        @click="store.initialize"
      >
        刷新日历 ↻</button
      ><button
        class="ci-btn ci-primary cursor-interaction"
        :disabled="exporting || loading || !feed?.tasks.length"
        @click="exportIcs"
      >
        {{ exporting ? '正在导出…' : '导出当前视图 .ics ↓' }}
      </button>
    </div>
  </div>
  <p class="ci-notice ci-before">
    按任务截止时刻展示 · {{ timezone }}。点击日期查看当天任务，点击任务进入详情。
  </p>
  <div class="ci-calendar-toolbar">
    <div class="ci-actions" aria-label="日历视图">
      <button
        class="ci-filter cursor-interaction"
        :aria-pressed="view === 'month'"
        @click="update({ view: 'month' })"
      >
        月视图</button
      ><button
        class="ci-filter cursor-interaction"
        :aria-pressed="view === 'week'"
        @click="update({ view: 'week' })"
      >
        周视图
      </button>
    </div>
    <div class="ci-actions">
      <button
        class="ci-btn cursor-interaction"
        :aria-label="view === 'month' ? '上个月' : '上一周'"
        @click="move(-1)"
      >
        ←</button
      ><button
        class="ci-btn cursor-interaction"
        @click="update({ date: dayKey(new Date(store.clock)) })"
      >
        回到今天</button
      ><button
        class="ci-btn cursor-interaction"
        :aria-label="view === 'month' ? '下个月' : '下一周'"
        @click="move(1)"
      >
        →
      </button>
    </div>
    <label class="ci-field"
      >查看日期<input type="date" class="ci-input" :value="selected" @change="chooseDate"
    /></label>
    <label class="ci-field"
      >按课程筛选日历<select class="ci-input" :value="course" @change="filter($event, 'course')">
        <option value="">全部课程</option>
        <option v-for="value in courses" :key="value" :value="value">{{ value }}</option>
        <option v-if="course && !courses.includes(course)" :value="course">{{ course }}</option>
      </select></label
    >
    <label class="ci-field"
      >按状态筛选日历<select class="ci-input" :value="scope" @change="filter($event, 'scope')">
        <option v-for="value in scopes" :key="value.id" :value="value.id">{{ value.label }}</option>
      </select></label
    >
  </div>
  <div v-if="error" class="ci-status ci-errorbanner" role="alert">
    {{ error
    }}<button class="ci-btn ci-text cursor-interaction" :disabled="loading" @click="load">
      重新读取 ↻
    </button>
  </div>
  <p v-if="exportMessage" class="ci-status" role="status">{{ exportMessage }}</p>
  <section class="ci-calendar-board" :aria-label="`${heading}截止日历`">
    <div class="ci-sectionhead">
      <h2>
        {{ heading }} <span class="ci-count">{{ feed?.tasks.length || 0 }} 项截止任务</span>
      </h2>
      <span class="ci-muted">周一开始 · 已选 {{ selected }}</span>
    </div>
    <p v-if="loading" class="ci-loading" role="status">正在读取截止时间…</p>
    <template v-else-if="feed">
      <div v-if="view === 'month'" class="ci-calendar-weekdays" aria-hidden="true">
        <span v-for="day in ['一', '二', '三', '四', '五', '六', '日']" :key="day"
          >周{{ day }}</span
        >
      </div>
      <div class="ci-calendar-grid" :class="{ 'ci-calendar-week': view === 'week' }">
        <div
          v-for="day in visibleDays"
          :key="dayKey(day)"
          class="ci-calendar-cell"
          :class="{
            'ci-calendar-outside': !inMonth(day),
            'ci-calendar-selected': dayKey(day) === selected,
          }"
        >
          <button
            class="ci-calendar-date cursor-interaction"
            :class="{ 'ci-calendar-today': dayKey(day) === dayKey(new Date(store.clock)) }"
            :aria-label="label(day)"
            :aria-pressed="dayKey(day) === selected"
            @click="update({ date: dayKey(day) })"
          >
            <span
              >{{ view === 'week' ? day.toLocaleDateString('zh-CN', { weekday: 'short' }) : '' }}
              {{ day.getDate() }}</span
            ><small v-if="grouped[dayKey(day)]?.length"
              >{{ grouped[dayKey(day)]!.length }} 项</small
            >
          </button>
          <div class="ci-calendar-chiplist">
            <RouterLink
              v-for="task in (grouped[dayKey(day)] || []).slice(
                0,
                view === 'month' ? 2 : undefined,
              )"
              :key="task.id"
              :to="`/tasks/${task.id}`"
              class="ci-calendar-chip"
              :class="{
                'ci-calendar-inactive': ['completed', 'cancelled', 'archived'].includes(
                  task.status,
                ),
              }"
              :title="`${task.title} · ${statusNames[task.status]}`"
              ><time>{{ clock(task.due_at!) }}</time> {{ courseEmoji(task.course) }}
              {{ task.title }}</RouterLink
            ><button
              v-if="view === 'month' && (grouped[dayKey(day)]?.length || 0) > 2"
              class="ci-calendar-more cursor-interaction"
              @click="update({ date: dayKey(day) })"
            >
              还有 {{ grouped[dayKey(day)]!.length - 2 }} 项 ↓
            </button>
          </div>
        </div>
      </div>
    </template>
  </section>
  <section v-if="feed" class="ci-calendar-agenda" aria-label="所选日期任务">
    <div class="ci-sectionhead">
      <h2>{{ selected }} 的安排 ✏️</h2>
      <span class="ci-count">{{ selectedTasks.length }} 项</span>
    </div>
    <TaskList :tasks="selectedTasks" />
  </section>
  <details v-if="feed" class="ci-calendar-undated">
    <summary class="cursor-interaction">📌 还没定截止时间 · {{ feed.undated.length }} 项</summary>
    <p class="ci-notice ci-before">
      这些任务符合当前课程和状态筛选，暂不放到具体日期，也不导出到日历。
    </p>
    <TaskList :tasks="feed.undated" />
  </details>
  <p class="ci-notice ci-calendar-exportnote">
    导出范围为当前{{
      view === 'month' ? '整月' : '整周'
    }}和当前筛选，只包含明确截止时间的任务。日历条目标记截止时刻，不额外设置时长或提醒。手动导入文件后不会自动同步；重复导入是否覆盖由日历客户端决定。
  </p>
</template>
