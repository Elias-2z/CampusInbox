import type { TaskInput } from '@/api'

const courseKey = (value?: string) => (value || '').replace(/\s+/g, '').toLowerCase()

function number(value: string): number {
  if (/^\d+$/.test(value)) return Number(value)
  const digit = (text: string) => (text === '两' ? 2 : '零一二三四五六七八九'.indexOf(text))
  if (value.includes('十')) {
    const [left, right] = value.split('十')
    return (left ? digit(left) : 1) * 10 + (right ? digit(right) : 0)
  }
  return Number([...value].map(digit).join(''))
}

function ordinals(title?: string): Map<string, Set<number>> {
  const result = new Map<string, Set<number>>()
  const normalized = (title || '').replace(/[^\p{L}\p{N}]/gu, '')
  const pattern =
    /第([一二三四五六七八九十两\d]+)(次|章|周|单元|讲)|(?:实验|作业|习题|练习|报告)([一二三四五六七八九十两\d]+)/g
  for (const match of normalized.matchAll(pattern)) {
    const key = match[2] || '次'
    const values = result.get(key) || new Set<number>()
    values.add(number(match[1] || match[3]!))
    result.set(key, values)
  }
  return result
}

export function canAssociate(source: Partial<TaskInput>, target: Partial<TaskInput>): boolean {
  if (
    courseKey(source.course) &&
    courseKey(target.course) &&
    courseKey(source.course) !== courseKey(target.course)
  )
    return false
  if (
    source.kind &&
    target.kind &&
    source.kind !== 'other' &&
    target.kind !== 'other' &&
    source.kind !== target.kind
  )
    return false
  const incoming = ordinals(source.title),
    existing = ordinals(target.title)
  for (const [key, values] of incoming) {
    const other = existing.get(key)
    if (other && (values.size !== other.size || [...values].some((value) => !other.has(value))))
      return false
  }
  return true
}
