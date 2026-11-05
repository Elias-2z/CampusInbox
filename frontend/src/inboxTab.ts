import type { InboxItem } from './api'

export function inboxTab(item: Pick<InboxItem, 'status' | 'classification'>): string {
  if (item.status === 'ignored' && item.classification?.decision === 'chat') return 'chat'
  return item.status === 'processing' ? 'pending' : item.status
}
