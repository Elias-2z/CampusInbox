export type TaskStatus = 'pending' | 'in_progress' | 'done' | 'submitted' | 'cancelled'
export interface Task {
  id: string
  title: string
  course: string
  scope: string
  deadline: string | null
  date_precision?: string
  status: TaskStatus
  version: number
  kind?: string
  checklist?: { text: string; done: boolean }[]
  checkin_verification?: string
  platform_check?: { status: string; suggested_status?: string }
  sample?: boolean
}
export interface Proposal {
  id: string
  candidate: { title: string; course: string; deadline: string | null }
  action: string
}
export interface CampusState {
  tasks: Task[]
  proposals: Proposal[]
  messages: { id: string; text: string; source: string; sample?: boolean }[]
  message_total: number
  summary: { upcoming: number; overdue: number; submitted: number; reminders?: Reminder[] }
  agent: { mode: string; description: string; provider?: string; model?: string }
}
export interface Reminder {
  task_id: string
  title: string
  deadline: string
  level: string
}
export const statusNames: Record<TaskStatus, string> = {
  pending: '待开始',
  in_progress: '进行中',
  done: '已做完',
  submitted: '已提交',
  cancelled: '已取消',
}
export function dayOf(value: string | Date | null): string {
  if (!value) return ''
  if (typeof value === 'string' && /^\d{4}-\d\d-\d\d$/.test(value)) return value
  const date = typeof value === 'string' ? new Date(value) : value
  if (!Number.isFinite(date.getTime())) return ''
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date)
  return ['year', 'month', 'day'].map((type) => parts.find((p) => p.type === type)?.value).join('-')
}
export function overdue(task: Task, now = new Date()): boolean {
  if (!task.deadline || ['submitted', 'cancelled'].includes(task.status)) return false
  return task.deadline.length === 10
    ? task.deadline < dayOf(now)
    : Date.parse(task.deadline) <= now.getTime()
}
export function activeTasks(tasks: Task[]): Task[] {
  return tasks.filter(
    (t) =>
      !['submitted', 'cancelled'].includes(t.status) &&
      !['unverified', 'ambiguous', 'ended'].includes(t.checkin_verification || ''),
  )
}
export function priorityTasks(tasks: Task[], now = new Date()): Task[] {
  const group = (task: Task) => (task.status === 'done' ? 1 : 0)
  return filterTasks(tasks, { status: 'active' }, now)
    .sort(
      (a, b) => group(a) - group(b) || (a.deadline || '9999').localeCompare(b.deadline || '9999'),
    )
    .slice(0, 5)
}
export function formatDeadline(value: string | null): string {
  if (!value) return '截止时间待补充'
  if (value.length === 10) return value.replaceAll('-', '/') + '（仅日期）'
  return new Date(value).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}
export function calendarDays(day: string, mode: 'month' | 'week'): string[] {
  const date = new Date(day + 'T12:00:00Z')
  if (mode === 'month') date.setUTCDate(1)
  date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7))
  return Array.from({ length: mode === 'month' ? 42 : 7 }, (_, i) =>
    new Date(date.getTime() + i * 86400000).toISOString().slice(0, 10),
  )
}
export function reminderItems(tasks: Task[], now = new Date(), leadMinutes = 1440): Task[] {
  return filterTasks(tasks, { status: 'active' }, now)
    .filter(
      (t) =>
        t.deadline &&
        (t.deadline.length === 10
          ? t.deadline <= dayOf(now)
          : Date.parse(t.deadline) - now.getTime() <= leadMinutes * 60000),
    )
    .sort((a, b) => (a.deadline || '').localeCompare(b.deadline || ''))
}
export function filterTasks(
  tasks: Task[],
  filters: { course?: string; search?: string; status?: string },
  now = new Date(),
): Task[] {
  const term = (filters.search || '').trim().toLowerCase()
  return tasks.filter(
    (t) =>
      (!filters.course || t.course === filters.course) &&
      (!term || `${t.title} ${t.course} ${t.scope}`.toLowerCase().includes(term)) &&
      (!filters.status ||
        filters.status === 'all' ||
        (filters.status === 'active'
          ? activeTasks([t]).length > 0 && !overdue(t, now)
          : filters.status === 'overdue'
            ? overdue(t, now)
            : t.status === filters.status)),
  )
}
