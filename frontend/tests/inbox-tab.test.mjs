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
const { inboxTab } = module.exports

test('routing to a diverted chat keeps the chat tab, including after reload', () => {
  assert.equal(inboxTab({ status: 'ignored', classification: { decision: 'chat' } }), 'chat')
  assert.equal(inboxTab({ status: 'ignored' }), 'ignored')
})

test('restored chat returns to pending, while other statuses retain their tabs', () => {
  assert.equal(inboxTab({ status: 'pending', classification: { decision: 'chat' } }), 'pending')
  assert.equal(inboxTab({ status: 'processing' }), 'pending')
  assert.equal(inboxTab({ status: 'accepted' }), 'accepted')
  assert.equal(inboxTab({ status: 'archived', classification: { decision: 'chat' } }), 'archived')
})
