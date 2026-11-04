import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import ts from 'typescript'
import { createPinia, setActivePinia } from 'pinia'

const require = createRequire(import.meta.url)
const source = ts.transpileModule(
  readFileSync(new URL('../src/stores/campus.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
).outputText

function campus(request) {
  setActivePinia(createPinia())
  const module = { exports: {} }
  new Function('require', 'module', 'exports', source)(
    (name) => (name === '@/api' ? { request } : require(name)),
    module,
    module.exports,
  )
  const store = module.exports.useCampus()
  store.ready = true
  return store
}

test('background intake appears and updates the pending badge without reinitializing', async () => {
  const store = campus(async () => [{ id: 'new-qq-notice', status: 'pending' }])
  await store.refreshInbox()
  assert.equal(store.inbox[0].id, 'new-qq-notice')
  assert.equal(store.pending.length, 1)
})

test('overlapping polls and polls during a mutation are skipped', async () => {
  let finish
  let requests = 0
  const store = campus(() => {
    requests++
    return new Promise((resolve) => (finish = resolve))
  })
  store.busy = true
  await store.refreshInbox()
  assert.equal(requests, 0)
  store.busy = false
  const first = store.refreshInbox()
  await store.refreshInbox()
  assert.equal(requests, 1)
  store.busy = true
  finish([{ id: 'outdated', status: 'pending' }])
  await first
  assert.equal(store.inbox.length, 0)
})

test('a stale poll cannot undo the results of a full refresh', async () => {
  let finish
  let requests = 0
  const store = campus((path) => {
    if (path === '/inbox?status=all') {
      requests++
      if (requests === 1) return new Promise((resolve) => (finish = resolve))
      return Promise.resolve([{ id: 'confirmed', status: 'accepted' }])
    }
    return Promise.resolve(path === '/tasks' ? [] : { items: [] })
  })
  const poll = store.refreshInbox()
  await store.refresh()
  finish([{ id: 'confirmed', status: 'pending' }])
  await poll
  assert.equal(store.inbox[0].status, 'accepted')
})

test('polling errors preserve messages, recover, and do not replace an operation error', async () => {
  let fail = true
  const store = campus(async () => {
    if (fail) throw new Error('offline')
    return [{ id: 'notice', status: 'pending' }]
  })
  store.inbox = [{ id: 'existing', status: 'pending' }]
  await store.refreshInbox()
  assert.equal(store.inbox[0].id, 'existing')
  assert.ok(store.error)
  fail = false
  await store.refreshInbox()
  assert.equal(store.error, '')
  store.error = 'An operation failed'
  await store.refreshInbox()
  assert.equal(store.error, 'An operation failed')
})
