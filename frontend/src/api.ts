export type TaskStatus =
  'todo' | 'in_progress' | 'done' | 'submitted' | 'completed' | 'cancelled' | 'archived'
export type TaskKind = 'homework' | 'exam' | 'event' | 'other'
export interface TaskInput {
  title: string
  course: string
  kind: TaskKind
  description: string
  due_at: string | null
  due_date: string | null
  submission_platform: string
  submission_method: string
  file_requirements: string
  location: string
  remarks: string
}
export interface Task extends TaskInput {
  recognition_method?: string
  checkin_closed?: boolean
  id: string
  suggestion_id: string
  message_id: string
  status: TaskStatus
  version: number
  created_at: string
  updated_at: string
}
export interface Evidence {
  method: string
  quote: string
  original?: Evidence | null
}
export interface Message {
  id: string
  text: string
  source: string
  sent_at: string | null
  timezone: string
  received_at: string
  attachments: AttachmentInfo[]
  source_context?:
    | {
        platform: 'legacy'
        namespace: string
        legacy_id: string
        time_known: boolean
        course: string
        group_name: string
      }
    | {
        platform: 'qq'
        group_id: string
        group_name: string
        sender_id: string
        sender_name: string
        course: string
      }
    | {
        platform: 'chaoxing'
        group_name: string
        sender_name: string
        course: string
        message_id: string
        kind: string
        closed_task_status?: boolean
        title?: string
        event_id?: string
        missing_originals?: { index: number; name: string; recoverable?: boolean }[]
      }
    | null
}
export interface AttachmentInfo {
  id: string
  filename: string
  size: number
  status: 'parsing' | 'ready' | 'failed'
}
export interface FileImport extends AttachmentInfo {
  text: string
  error: string
}
export interface ImportCapabilities {
  mineru_configured: boolean
  local_ocr: boolean
  local_docx: boolean
  max_bytes: number
  extensions: string[]
}
export interface InboxItem {
  activity_schedule?: string[]
  id: string
  status: 'processing' | 'pending' | 'accepted' | 'ignored' | 'archived'
  action: 'create' | 'review' | 'ignore'
  method: string
  fields: Partial<TaskInput>
  evidence: Record<string, Evidence>
  warnings: string[]
  message: Message
  matched_tasks: Task[]
  task_id: string | null
  updated_at: string
  merge_change: MergeChange | null
  cancellation_supported: boolean
  cancellation_change: DeadlineChange | null
  split_supported: boolean
  split_items: SplitDraft[]
  deadline_supported: boolean
  deadline_change: DeadlineChange | null
}
export interface SplitDraft {
  merge_change: MergeChange | null
  id: string
  position: number
  quote: string
  status: 'pending' | 'accepted' | 'ignored'
  can_create: boolean
  fields: Partial<TaskInput>
  evidence: Record<string, Evidence>
  warnings: string[]
  matched_tasks: Task[]
  task_id: string | null
  updated_at: string
}
export interface TaskEvent {
  id: string
  message_id: string
  operation: string
  before: Record<string, unknown>
  after: Record<string, unknown>
  actor: string
  created_at: string
}
export interface TaskDetail {
  checkin_closed?: boolean
  legacy?: {
    namespace: string
    legacy_id: string
    record: { history?: { at?: string; action?: string; before?: unknown; after?: unknown }[] }
  } | null
  task: Task
  platform_observation: {
    id: string
    status: string
    checked_at: string
    suggested_status: 'todo' | 'submitted' | null
  } | null
  materials: { text: string; done: boolean }[]
  source: Message
  sources: Message[]
  deadline_changes: DeadlineChange[]
  cancellation_changes: DeadlineChange[]
  merge_changes: MergeChange[]
  events: TaskEvent[]
  evidence: Record<string, Evidence>
  method: string
}
export interface DeadlinePreview {
  id: string
  suggestion_id: string
  task: Task
  proposed_due_at: string | null
  proposed_due_date: string | null
  warnings: string[]
  evidence: Evidence
  created_at: string
}
export interface CancellationPreview {
  id: string
  suggestion_id: string
  task: Task
  proposed_status: 'cancelled'
  created_at: string
}
export interface DeadlineChange {
  suggestion_id: string
  task: Task
  event_id: string
  undo_event_id: string | null
  applied_version: number
  can_undo: boolean
  reverted: boolean
}
export interface MergePreview {
  id: string
  task: Task
  before_fields: Partial<TaskInput>
  after_fields: Partial<TaskInput>
  warnings: string[]
  created_at: string
}
export interface MergeChange extends DeadlineChange {
  id: string
  split_draft_id: string | null
}
export interface CalendarFeed {
  start: string
  end: string
  timezone: string
  tasks: Task[]
  undated: Task[]
}
export async function downloadCalendar(query: URLSearchParams): Promise<void> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), 30000)
  try {
    const response = await fetch(`/api/calendar/export.ics?${query}`, { signal: controller.signal })
    if (!response.ok) {
      const data = await response.json().catch(() => null)
      throw new Error(
        typeof data?.detail === 'string' ? data.detail : '日历导出未完成，请稍后重试。',
      )
    }
    if (!response.headers.get('Content-Type')?.startsWith('text/calendar'))
      throw new Error('日历文件格式不正确，请刷新后重试。')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download =
      response.headers.get('Content-Disposition')?.match(/filename="([a-zA-Z0-9._-]+)"/)?.[1] ||
      'campusinbox.ics'
    document.body.append(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 60000)
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError')
      throw new Error('日历导出超时，请重试。')
    throw error
  } finally {
    window.clearTimeout(timer)
  }
}
export interface ReminderConfig {
  enabled: boolean
  lead_minutes: number
  overdue_enabled: boolean
}
export interface Reminder {
  id: string
  task: Task
  phase: 'upcoming' | 'overdue'
  status: 'unread' | 'read' | 'snoozed'
  due_at: string | null
  due_date: string | null
  read_at: string | null
  snoozed_until: string | null
  version: number
}
export interface ReminderFeed {
  config: ReminderConfig
  items: Reminder[]
  evaluated_at: string
}
export interface ModelConfig {
  enabled: boolean
  base_url: string
  model: string
  timeout_seconds: number
}
export interface Probe {
  reachable: boolean
  models: string[]
  error: string | null
}
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}
export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), 135000)
  try {
    const response = await fetch(`/api${path}`, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...options.headers },
      signal: controller.signal,
    })
    const data = await response.json().catch(() => null)
    if (!response.ok) {
      const detail = data?.detail
      const message =
        typeof detail === 'string'
          ? detail
          : Array.isArray(detail)
            ? detail.map((e: { msg: string }) => e.msg).join('；')
            : `请求未成功（${response.status}），请稍后重试。`
      throw new ApiError(response.status, message)
    }
    if (data === null) throw new Error('服务返回了无法读取的结果，请检查后端是否启动。')
    return data as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError')
      throw new Error('请求超时。原文可能已保存，请先刷新收件箱核对。')
    if (error instanceof TypeError)
      throw new Error('暂时连接不上本地服务，请确认后端已启动，再点击刷新。')
    throw error
  } finally {
    window.clearTimeout(timer)
  }
}
export function post<T>(path: string, data?: unknown) {
  return request<T>(path, {
    method: 'POST',
    body: data === undefined ? undefined : JSON.stringify(data),
  })
}
export const emptyDraft = (): TaskInput => ({
  title: '',
  course: '',
  kind: 'other',
  description: '',
  due_at: '',
  due_date: '',
  submission_platform: '',
  submission_method: '',
  file_requirements: '',
  location: '',
  remarks: '',
})
export function localInput(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return ''
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}T${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}
export function taskInput(task: Partial<TaskInput>): TaskInput {
  const result = emptyDraft()
  for (const key of Object.keys(result) as (keyof TaskInput)[]) {
    if (task[key] !== undefined) Object.assign(result, { [key]: task[key] })
  }
  return result
}
export const draftOf = (task: Partial<TaskInput> = {}): TaskInput => ({
  ...taskInput(task),
  due_at: localInput(task.due_at ?? null),
})
export const serialize = (draft: TaskInput): TaskInput => ({
  ...draft,
  title: draft.title.trim(),
  due_at: draft.due_at ? new Date(draft.due_at).toISOString() : null,
  due_date: draft.due_date || null,
})
export const statusNames: Record<TaskStatus, string> = {
  todo: '待办',
  in_progress: '进行中',
  done: '已做完，未提交',
  submitted: '已提交',
  completed: '已完成',
  cancelled: '已取消',
  archived: '已归档',
}
export const fieldNames: Record<string, string> = {
  title: '任务名称',
  course: '课程',
  kind: '类型',
  description: '任务内容',
  due_at: '截止时间',
  due_date: '截止日期',
  materials: '材料清单',
  status: '状态',
  submission_platform: '提交平台',
  submission_method: '提交方式',
  file_requirements: '文件要求',
  location: '地点',
  remarks: '备注',
}
export function displayDate(value: string | null, withYear = false): string {
  if (!value) return '截止时间待补充'
  return new Date(value).toLocaleString('zh-CN', {
    ...(withYear ? { year: 'numeric' as const } : {}),
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}
export function displayDeadline(
  value: { due_at: string | null; due_date?: string | null },
  withYear = false,
): string {
  if (value.due_date) return `${value.due_date.replaceAll('-', '/')}（仅日期）`
  return displayDate(value.due_at, withYear)
}
export function displayMessageTime(message: Message, withYear = false): string {
  if (
    message.sent_at === null ||
    (message.source_context?.platform === 'legacy' && !message.source_context.time_known)
  )
    return '发布时间未知'
  return displayDate(message.sent_at, withYear)
}
export function deadlineKey(value: {
  due_at: string | null
  due_date?: string | null
}): string | null {
  if (value.due_date) return value.due_date
  if (!value.due_at) return null
  const d = new Date(value.due_at)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
export function deadlineExpired(
  value: { due_at: string | null; due_date?: string | null },
  now: number,
): boolean {
  if (value.due_date) {
    const today = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(
      new Date(now),
    )
    return value.due_date < today
  }
  return !!value.due_at && Date.parse(value.due_at) <= now
}
export function deadlineOrder(value: { due_at: string | null; due_date?: string | null }): number {
  return value.due_date
    ? Date.parse(`${value.due_date}T23:59:59+08:00`)
    : value.due_at
      ? Date.parse(value.due_at)
      : Infinity
}
export const dayKey = (date: Date) => `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`
export const courseEmoji = (course: string) =>
  ({
    数据库原理: '💾',
    数据库: '💾',
    软件工程: '🧩',
    大学英语: '📚',
    高等数学: '🧮',
    数据结构: '🌳',
  })[course] || '📝'
