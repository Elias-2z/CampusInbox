import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source = ts.transpileModule(
  readFileSync(new URL('../src/inboxTab.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS } },
).outputText
const module = { exports: {} }
new Function('module', 'exports', source)(module, module.exports)
const { inboxTab, inboxMatchesTab, inboxTabs } = module.exports

test('automatic chat and manually ignored notifications share ignored messages', () => {
  assert.equal(inboxTab({ status: 'ignored', classification: { decision: 'chat' } }), 'ignored')
  assert.equal(
    inboxTab({ status: 'ignored', classification: { decision: 'notification' } }),
    'ignored',
  )
  assert.equal(inboxTab({ status: 'ignored' }), 'ignored')
})

test('restored chat, unknown classification and in-flight analysis need review', () => {
  assert.equal(inboxTab({ status: 'pending', classification: { decision: 'chat' } }), 'uncertain')
  assert.equal(
    inboxTab({ status: 'processing', classification: { decision: 'notification' } }),
    'uncertain',
  )
  assert.equal(inboxTab({ status: 'pending' }), 'uncertain')
  assert.equal(
    inboxTab({ status: 'pending', classification: { decision: 'uncertain' } }),
    'uncertain',
  )
})

test('identified notifications stay important before and after confirmation', () => {
  assert.equal(
    inboxTab({ status: 'pending', classification: { decision: 'notification' } }),
    'important',
  )
  assert.equal(
    inboxTab({ status: 'accepted', classification: { decision: 'uncertain' } }),
    'important',
  )
  assert.equal(inboxTab({ status: 'accepted' }), 'important')
})

test('categories do not overlap and archived messages remain accessible in all', () => {
  const messages = [
    { status: 'pending', classification: { decision: 'notification' } },
    { status: 'pending', classification: { decision: 'uncertain' } },
    { status: 'ignored', classification: { decision: 'notification' } },
    { status: 'archived', classification: { decision: 'notification' } },
  ]
  assert.deepEqual(
    inboxTabs.map((t) => t.title),
    ['全部消息', '重要消息', '无法确认消息', '忽略消息'],
  )
  for (const item of messages) {
    assert.equal(inboxMatchesTab(item, 'all'), true)
    const categories = inboxTabs.filter((t) => t.id !== 'all' && inboxMatchesTab(item, t.id))
    assert.equal(categories.length, item.status === 'archived' ? 0 : 1)
  }
})
