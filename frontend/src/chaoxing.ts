import { ref } from 'vue'
import { localInput, post, request } from '@/api'

export interface ChaoxingStatus {
  task_sections?: {
    key: string
    course: string
    course_index: number
    kind: 'assignment' | 'exam'
    state: 'unchecked' | 'queued' | 'partial' | 'checked' | 'failed'
  }[]
  enabled: boolean
  state: string
  error: string
  qr_ready: boolean
  last_received: number | null
  freshness: 'paused' | 'offline' | 'starting' | 'stale' | 'fresh'
  last_heartbeat: number | null
  sources: Record<
    'notice' | 'chat' | 'task_center',
    {
      state: string
      coverage: string
      checked_count: number
      captured_count: number
      failed_count: number
      pending_count: number
      detail_pending_count: number
      unsupported_detail_count: number
      media_pending_count: number
      last_checked: number | null
      last_complete: number | null
      issue_count?: number
      issues?: {
        key: string
        notice_index: number
        detail_pending: boolean
        media_pending_count: number
      }[]
    }
  >
  counts: {
    pending: number
    done: number
    failed: number
    needs_time: number
    needs_review: number
  }
}

export interface ChaoxingEvent {
  id: string
  state: 'needs_time' | 'needs_review' | 'failed' | 'done' | 'pending'
  kind: string
  title: string
  text: string
  course: string
  display_time: string
  attachments: { index: number; name: string; available: boolean; fetchable?: boolean }[]
}

const base = '/connectors/chaoxing'

export function createChaoxingConnection(onImported: () => void) {
  const status = ref<ChaoxingStatus | null>(null)
  const events = ref<ChaoxingEvent[]>([])
  const busy = ref(false)
  const loading = ref(false)
  const error = ref('')
  const refreshError = ref('')
  const qrVersion = ref(0)
  let timer: number | undefined
  let active = false
  let version = 0

  async function refresh() {
    if (!active || busy.value || loading.value) return
    const current = ++version
    loading.value = true
    try {
      const [nextStatus, nextEvents] = await Promise.all([
        request<ChaoxingStatus>(base),
        request<ChaoxingEvent[]>(`${base}/events`),
      ])
      if (!active || current !== version) return
      status.value = nextStatus
      events.value = nextEvents
      if (nextStatus.qr_ready) qrVersion.value++
      refreshError.value = ''
    } catch (cause) {
      if (active && current === version) {
        refreshError.value = cause instanceof Error ? cause.message : '状态读取失败，请重试。'
      }
    } finally {
      if (current === version) loading.value = false
    }
  }

  async function run(path: string, body?: unknown, eventAction = false) {
    if (!active || busy.value) return
    version++
    loading.value = false
    busy.value = true
    error.value = ''
    try {
      const result = await post<{ state: string }>(`${base}${path}`, body)
      if (active && eventAction && result.state === 'done') onImported()
    } catch (cause) {
      if (active) error.value = cause instanceof Error ? cause.message : '操作失败，请重试。'
    } finally {
      busy.value = false
      if (active) await refresh()
    }
  }

  function start() {
    if (!active) {
      active = true
      timer = window.setInterval(refresh, 5000)
    }
    return refresh()
  }
  function stop() {
    active = false
    version++
    window.clearInterval(timer)
    timer = undefined
  }
  function perform(
    action: 'connect' | 'pause' | 'disconnect' | 'recheck',
    body?: { visible: boolean },
  ) {
    return run(`/${action}`, body)
  }
  function resolve(id: string, value: string) {
    const date = new Date(value)
    if (!value || Number.isNaN(date.valueOf()) || localInput(date.toISOString()) !== value) {
      error.value = '请填写有效的真实发布时间，按本机时区输入。'
      return Promise.resolve()
    }
    return run(`/events/${encodeURIComponent(id)}/resolve`, { sent_at: date.toISOString() }, true)
  }
  function retry(id: string) {
    return run(`/events/${encodeURIComponent(id)}/retry`, undefined, true)
  }
  function fetchOriginal(id: string, index: number) {
    return run(`/events/${encodeURIComponent(id)}/attachments/${index}/fetch`, undefined, true)
  }
  function recheckSection(key: string) {
    return run('/sections/recheck', { key })
  }
  function recheckNotice(key: string) {
    return run('/notices/recheck', { key })
  }
  return {
    status,
    events,
    busy,
    loading,
    error,
    refreshError,
    qrVersion,
    start,
    stop,
    refresh,
    perform,
    resolve,
    retry,
    fetchOriginal,
    recheckSection,
    recheckNotice,
  }
}
