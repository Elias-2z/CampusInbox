import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { createServer } from 'vite'

let vite
let addMaterialCandidate
before(async () => {
  vite = await createServer({
    configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  })
  ;({ addMaterialCandidate } = await vite.ssrLoadModule('/src/materialSuggestions.ts'))
})
after(async () => await vite?.close())

test('adds a candidate once without changing checked materials', () => {
  const list = [{ text: '实验报告', done: true }]
  assert.deepEqual(addMaterialCandidate(list, ' 实验报告 '), list)
  assert.deepEqual(addMaterialCandidate(list, '源代码'), [
    { text: '实验报告', done: true },
    { text: '源代码', done: false },
  ])
  assert.equal(list.length, 1)
})

test('rejects blank suggestions and respects the 100 item limit', () => {
  const full = Array.from({ length: 100 }, (_, n) => ({ text: String(n), done: false }))
  assert.equal(addMaterialCandidate([], '  ').length, 0)
  assert.equal(addMaterialCandidate(full, 'new').length, 100)
})
