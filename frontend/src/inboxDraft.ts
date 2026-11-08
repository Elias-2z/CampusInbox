import type { TaskInput } from '@/api'

export function refreshInboxDraft(
  current: TaskInput,
  previous: TaskInput,
  incoming: TaskInput,
): TaskInput {
  const refreshed = { ...incoming }
  for (const key of Object.keys(current) as (keyof TaskInput)[]) {
    if (current[key] !== previous[key]) Object.assign(refreshed, { [key]: current[key] })
  }
  // Date and time form one deadline; preserve both when either was manually edited.
  if (current.due_at !== previous.due_at || current.due_date !== previous.due_date) {
    refreshed.due_at = current.due_at
    refreshed.due_date = current.due_date
  }
  return refreshed
}
