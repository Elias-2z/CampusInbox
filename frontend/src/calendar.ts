/** Local calendar dates use noon anchors so DST does not shift day navigation. */
export type CalendarView = 'month' | 'week'
export function dayKey(value: Date): string {
  return `${String(value.getFullYear()).padStart(4, '0')}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`
}
export function parseDay(value: unknown): Date | null {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null
  const date = new Date(`${value}T12:00:00`)
  return !Number.isNaN(date.valueOf()) &&
    date.getFullYear() >= 1 &&
    date.getFullYear() <= 9999 &&
    dayKey(date) === value
    ? date
    : null
}
export function addDays(value: Date, count: number): Date {
  const result = new Date(value)
  result.setHours(12, 0, 0, 0)
  result.setDate(result.getDate() + count)
  return result
}
export function monthStart(value: Date): Date {
  const result = addDays(value, 0)
  result.setDate(1)
  return result
}
export function shiftMonth(value: Date, count: number): Date {
  const result = monthStart(value)
  result.setMonth(result.getMonth() + count)
  const last = new Date(result)
  last.setMonth(last.getMonth() + 1, 0)
  result.setDate(Math.min(value.getDate(), last.getDate()))
  return result
}
export function weekStart(value: Date): Date {
  return addDays(value, -((value.getDay() + 6) % 7))
}
export function range(value: Date, view: CalendarView): { start: string; end: string } {
  const start = view === 'month' ? monthStart(value) : weekStart(value)
  const end = view === 'month' ? shiftMonth(start, 1) : addDays(start, 7)
  return { start: dayKey(start), end: dayKey(end) }
}
export function days(value: Date, view: CalendarView): Date[] {
  const start = view === 'month' ? weekStart(monthStart(value)) : weekStart(value)
  return Array.from({ length: view === 'month' ? 42 : 7 }, (_, index) => addDays(start, index))
}
