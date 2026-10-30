export type TaskStatus = 'todo' | 'in_progress' | 'completed' | 'cancelled' | 'archived'
export type TaskKind = 'homework' | 'exam' | 'event' | 'other'
export interface TaskInput {
  title: string
  course: string
  kind: TaskKind
  description: string
  due_at: string | null
  submission_platform: string
  submission_method: string
  file_requirements: string
  location: string
  remarks: string
}
export interface Task extends TaskInput {
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
  sent_at: string
  timezone: string
  received_at: string
}
export interface InboxItem {
  id: string
  status: 'processing' | 'pending' | 'accepted' | 'ignored'
  action: 'create' | 'review' | 'ignore'
  method: string
  fields: Partial<TaskInput>
  evidence: Record<string, Evidence>
  warnings: string[]
  message: Message
  matched_tasks: Task[]
  task_id: string | null
  updated_at: string
  cancellation_supported: boolean
  cancellation_change: DeadlineChange | null
  deadline_supported: boolean
  deadline_change: DeadlineChange | null
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
  task: Task
  source: Message
  sources: Message[]
  deadline_changes: DeadlineChange[]
  cancellation_changes: DeadlineChange[]
  events: TaskEvent[]
  evidence: Record<string, Evidence>
  method: string
}
export interface DeadlinePreview {
  id: string
  suggestion_id: string
  task: Task
  proposed_due_at: string
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
})
export const statusNames: Record<TaskStatus, string> = {
  todo: '待办',
  in_progress: '进行中',
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
