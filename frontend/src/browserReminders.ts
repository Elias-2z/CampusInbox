import { displayDeadline, type Reminder } from '@/api'

const ENABLED = 'campusinbox-browser-reminders-enabled'
const SEEN = 'campusinbox-browser-reminders-seen'

function key(item: Reminder): string {
  return `${item.id}:${item.version}`
}

function readSeen(storage: Storage): string[] {
  try {
    const values: unknown = JSON.parse(storage.getItem(SEEN) || '[]')
    return Array.isArray(values)
      ? values.filter((value): value is string => typeof value === 'string')
      : []
  } catch {
    return []
  }
}

export function browserRemindersEnabled(storage: Storage): boolean {
  try {
    return storage.getItem(ENABLED) === 'true'
  } catch {
    return false
  }
}

export async function enableBrowserReminders(
  storage: Storage,
  notifications: typeof Notification,
  items: Reminder[],
): Promise<boolean> {
  if ((await notifications.requestPermission()) !== 'granted') return false
  const baseline = items.filter((item) => item.status === 'unread').map(key)
  storage.setItem(SEEN, JSON.stringify(baseline.slice(-5000)))
  storage.setItem(ENABLED, 'true')
  return true
}

export function disableBrowserReminders(storage: Storage): void {
  storage.removeItem(ENABLED)
}

export function dispatchBrowserReminders(
  storage: Storage,
  notifications: typeof Notification,
  items: Reminder[],
): void {
  if (!browserRemindersEnabled(storage) || notifications.permission !== 'granted') return
  const seen = new Set(readSeen(storage))
  for (const item of items) {
    const occurrence = key(item)
    if (item.status !== 'unread' || seen.has(occurrence)) continue
    try {
      // Save before displaying so storage failures do not cause repeated notifications.
      seen.add(occurrence)
      storage.setItem(SEEN, JSON.stringify([...seen].slice(-5000)))
      new notifications(item.task.title, {
        body: `${item.phase === 'overdue' ? '已逾期' : '即将截止'} · ${item.task.course || '待分类'} · ${displayDeadline(item, true)}`,
        tag: `campusinbox:${occurrence}`,
      })
    } catch {
      // Browsers may deny notifications or storage; in-app reminders remain available.
      return
    }
  }
}
