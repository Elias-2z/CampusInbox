import type { InboxItem } from './api'

export const inboxTabs = [
  { id: 'all', title: '全部消息' },
  { id: 'important', title: '重要消息' },
  { id: 'uncertain', title: '无法确认消息' },
  { id: 'ignored', title: '忽略消息' },
] as const

type MessageCategory = Pick<InboxItem, 'status' | 'classification'>

export function inboxTab(item: MessageCategory): string {
  if (item.status === 'ignored') return 'ignored'
  if (item.status === 'archived') return 'all'
  if (item.status === 'processing') return 'uncertain'
  if (item.status === 'accepted' || item.classification?.decision === 'notification')
    return 'important'
  return 'uncertain'
}

export function inboxMatchesTab(item: MessageCategory, tab: string): boolean {
  return tab === 'all' || inboxTab(item) === tab
}
