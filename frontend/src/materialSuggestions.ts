export type Material = { text: string; done: boolean }
export type MaterialCandidate = { text: string; quote: string; source: string }

export function addMaterialCandidate(items: Material[], value: string): Material[] {
  const text = value.trim()
  if (!text || items.length >= 100 || items.some((item) => item.text === text)) return items
  return [...items, { text, done: false }]
}
