import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  post,
  request,
  taskInput,
  type DeadlineChange,
  type MergeChange,
  type InboxItem,
  type Task,
  type TaskStatus,
  type Reminder,
  type ReminderFeed,
} from '@/api'

export const useCampus = defineStore('campus', () => {
  const tasks = ref<Task[]>([])
  const inbox = ref<InboxItem[]>([])
  const loading = ref(false)
  const ready = ref(false)
  const busy = ref(false)
  const error = ref('')
  const notice = ref('')
  const importing = ref(false)
  const editorOpen = ref(false)
  const editorTask = ref<Task | null>(null)
  const clock = ref(Date.now())
  const celebration = ref(false)
  const reminderFeed = ref<ReminderFeed | null>(null)
  const reminderLoading = ref(false)
  const reminderBusy = ref('')
  const reminderError = ref('')
  const reminderStale = ref(false)
  const unreadReminders = computed(
    () => reminderFeed.value?.items.filter((item) => item.status === 'unread') || [],
  )
  let reminderRefreshVersion = 0
  async function refreshReminders() {
    const version = ++reminderRefreshVersion
    reminderLoading.value = true
    try {
      const result = await request<ReminderFeed>('/reminders')
      if (version !== reminderRefreshVersion) return
      reminderFeed.value = result
      reminderError.value = ''
      reminderStale.value = false
    } catch (e) {
      if (version === reminderRefreshVersion) {
        reminderError.value = errorMessage(e)
        reminderStale.value = true
      }
    } finally {
      if (version === reminderRefreshVersion) reminderLoading.value = false
    }
  }
  async function actOnReminder(
    item: Reminder,
    operation: 'read' | 'snooze' | 'restore',
    minutes = 30,
  ) {
    if (reminderBusy.value) return
    reminderBusy.value = item.id
    reminderError.value = ''
    try {
      await post<Reminder>(`/reminders/${item.id}/${operation}`, {
        expected_version: item.version,
        snooze_minutes: minutes,
      })
      await refreshReminders()
    } catch (e) {
      const message = errorMessage(e)
      await refreshReminders()
      reminderError.value = message
    } finally {
      reminderBusy.value = ''
    }
  }
  const pending = computed(() =>
    inbox.value.filter((i) => ['pending', 'processing'].includes(i.status)),
  )
  const active = computed(() =>
    tasks.value.filter((t) => ['todo', 'in_progress'].includes(t.status)),
  )
  let undoAction: (() => Promise<unknown>) | null = null
  const canUndo = ref(false)
  let celebrationTimer: number | undefined
  let refreshVersion = 0
  async function refresh() {
    const version = ++refreshVersion
    const [nextTasks, nextInbox] = await Promise.all([
      request<Task[]>('/tasks'),
      request<InboxItem[]>('/inbox?status=all'),
    ])
    if (version !== refreshVersion) return
    tasks.value = nextTasks
    inbox.value = nextInbox
    ready.value = true
    await refreshReminders()
  }
  async function initialize() {
    loading.value = true
    error.value = ''
    try {
      await refresh()
    } catch (e) {
      error.value = errorMessage(e)
    } finally {
      loading.value = false
    }
  }
  function errorMessage(e: unknown) {
    return e instanceof Error ? e.message : '操作未完成，请稍后重试。'
  }
  async function mutate<T>(operation: () => Promise<T>, message: string): Promise<T | undefined> {
    if (busy.value) return undefined
    busy.value = true
    error.value = ''
    notice.value = ''
    undoAction = null
    canUndo.value = false
    try {
      const result = await operation()
      notice.value = message
      try {
        await refresh()
      } catch {
        error.value = '操作已保存，但列表刷新失败，请点击刷新。'
        await refreshReminders()
      }
      return result
    } catch (e) {
      error.value = errorMessage(e)
      return undefined
    } finally {
      busy.value = false
    }
  }
  function setUndo(action: () => Promise<unknown>) {
    undoAction = action
    canUndo.value = true
  }
  async function undo() {
    const action = undoAction
    if (action) await mutate(action, '已恢复到上一步，操作记录已保留。')
  }
  function edit(task: Task | null = null) {
    editorTask.value = task
    editorOpen.value = true
  }
  async function changeStatus(task: Task, status: TaskStatus) {
    const updated = await mutate(
      () =>
        request<Task>(`/tasks/${task.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ ...taskInput(task), status, expected_version: task.version }),
        }),
      status === 'completed' ? '🎉 又搞定一件！给自己一个赞。' : '任务状态已更新。',
    )
    if (updated) {
      setUndo(() =>
        request(`/tasks/${task.id}`, {
          method: 'PATCH',
          body: JSON.stringify({
            ...taskInput(task),
            status: task.status,
            expected_version: updated.version,
          }),
        }),
      )
      if (status === 'completed') {
        window.clearTimeout(celebrationTimer)
        celebration.value = true
        celebrationTimer = window.setTimeout(() => {
          celebration.value = false
        }, 1000)
      }
    }
  }
  async function archive(task: Task) {
    const updated = await mutate(
      () =>
        request<Task>(`/tasks/${task.id}?expected_version=${task.version}`, { method: 'DELETE' }),
      '任务已归档，可以在“已归档”中恢复。',
    )
    if (updated)
      setUndo(() =>
        request(`/tasks/${task.id}`, {
          method: 'PATCH',
          body: JSON.stringify({
            ...taskInput(task),
            status: task.status,
            expected_version: updated.version,
          }),
        }),
      )
  }
  function registerMergeUndo(change: MergeChange) {
    if (change.can_undo) setUndo(() => post(`/merge-changes/${change.id}/undo`))
  }
  async function undoMerge(change: MergeChange) {
    await mutate(
      () => post<MergeChange>(`/merge-changes/${change.id}/undo`),
      '本次关联与字段补充已撤销，原文和历史仍保留。',
    )
  }
  function registerCancellationUndo(change: DeadlineChange) {
    if (change.can_undo) setUndo(() => post(`/cancellation-changes/${change.suggestion_id}/undo`))
  }
  async function undoCancellation(change: DeadlineChange) {
    await mutate(
      () => post<DeadlineChange>(`/cancellation-changes/${change.suggestion_id}/undo`),
      '本次取消已撤销，任务已恢复原状态。',
    )
  }
  function registerDeadlineUndo(change: DeadlineChange) {
    if (change.can_undo) setUndo(() => post(`/deadline-changes/${change.suggestion_id}/undo`))
  }
  async function undoDeadline(change: DeadlineChange) {
    await mutate(
      () => post<DeadlineChange>(`/deadline-changes/${change.suggestion_id}/undo`),
      '本次截止时间更新已撤销，通知和历史记录仍保留。',
    )
  }
  async function ignore(item: InboxItem) {
    const updated = await mutate(
      () => post<InboxItem>(`/suggestions/${item.id}/ignore`),
      '已忽略这条建议，原文仍然保留。',
    )
    if (updated) setUndo(() => post(`/suggestions/${item.id}/reopen`))
  }
  return {
    tasks,
    inbox,
    reminderFeed,
    reminderLoading,
    reminderBusy,
    reminderError,
    reminderStale,
    unreadReminders,
    refreshReminders,
    actOnReminder,
    pending,
    active,
    ready,
    loading,
    busy,
    error,
    notice,
    importing,
    editorOpen,
    editorTask,
    clock,
    celebration,
    canUndo,
    refresh,
    initialize,
    mutate,
    undo,
    edit,
    changeStatus,
    archive,
    ignore,
    registerMergeUndo,
    undoMerge,
    registerCancellationUndo,
    undoCancellation,
    registerDeadlineUndo,
    undoDeadline,
    errorMessage,
  }
})
