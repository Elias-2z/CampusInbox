import { ref } from 'vue'
import type { CampusState, Task, TaskStatus } from './domain'
export const state = ref<CampusState | null>(null)
export const error = ref('')
export const loading = ref(false)
export const connected = ref(false)
export interface FileImport {
  id: string
  filename: string
  size: number
  status: 'uploading' | 'parsing' | 'ready' | 'failed'
  text: string
  error: string
  message_id: string | null
}
export interface ImportCapabilities {
  mineru_configured: boolean
  max_bytes: number
  extensions: string[]
}
let token = sessionStorage.getItem('courseinbox-token') || ''
let refreshVersion = 0
export function setToken(value: string) {
  token = value.trim()
  sessionStorage.setItem('courseinbox-token', token)
}
export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch('/api' + path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: 'Bearer ' + token,
      ...options.headers,
    },
    signal: AbortSignal.timeout(135000),
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    if (response.status === 401) connected.value = false
    throw new Error(data?.error || `请求失败（${response.status}）`)
  }
  if (data === null) throw new Error('服务返回的数据无法读取')
  return data as T
}
export async function initialize() {
  loading.value = true
  try {
    const bootstrap = await fetch('/api/bootstrap', { signal: AbortSignal.timeout(5000) })
    if (bootstrap.ok) setToken((await bootstrap.json()).token)
    await refresh()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '无法连接本地服务'
  } finally {
    loading.value = false
  }
}
export function post<T>(path: string, body: unknown = {}) {
  return request<T>(path, { method: 'POST', body: JSON.stringify(body) })
}
export async function refresh() {
  const version = ++refreshVersion
  const next = await request<CampusState>('/state')
  if (version === refreshVersion) {
    state.value = next
    connected.value = true
    error.value = ''
  }
}
export async function changeStatus(task: Task, status: TaskStatus) {
  const result = await request<Task>(`/tasks/${task.id}`, {
    method: 'PATCH',
    body: JSON.stringify({ status, expected_version: task.version }),
  })
  await refresh()
  return result
}
export async function download(path: string, filename: string) {
  const response = await fetch('/api' + path, {
    headers: { Authorization: 'Bearer ' + token },
    signal: AbortSignal.timeout(30000),
  })
  if (!response.ok) throw new Error('导出失败，请检查服务连接')
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
