import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const module = { exports: {} }
const code = ts.transpileModule(
  readFileSync(new URL('../src/inboxDraft.ts', import.meta.url), 'utf8'),
  {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  },
).outputText
new Function('module', 'exports', code)(module, module.exports)
const { refreshInboxDraft } = module.exports

test('attachment fields refresh while preserving a manually edited task title', () => {
  const previous = { title: '', due_at: null, due_date: null, course: '数据库' }
  const current = { ...previous, title: '我的实验报告' }
  const incoming = { ...previous, title: '实验报告', due_at: '2099-12-10T20:00' }
  assert.deepEqual(refreshInboxDraft(current, previous, incoming), {
    ...incoming,
    title: '我的实验报告',
  })
})

test('a manually edited deadline remains coherent when attachment supplies a precise clock', () => {
  const previous = { title: '实验报告', due_at: null, due_date: null }
  const current = { ...previous, due_date: '2099-12-09' }
  const incoming = { ...previous, due_at: '2099-12-10T20:00' }
  assert.deepEqual(refreshInboxDraft(current, previous, incoming), current)
})
