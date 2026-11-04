import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { createServer } from 'vite'

let vite
let coverageLabel
before(async () => {
  vite = await createServer({
    configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  })
  ;({ coverageLabel } = await vite.ssrLoadModule('/src/chaoxingHealth.ts'))
})
after(async () => await vite?.close())

test('coverage labels distinguish checked text from partial and unknown coverage', () => {
  assert.match(coverageLabel('complete'), /本轮/)
  assert.match(coverageLabel('loaded_text'), /文字/)
  assert.match(coverageLabel('partial'), /部分/)
  assert.match(coverageLabel('list_only'), /列表/)
  assert.match(coverageLabel('unexpected'), /无法确认/)
})
