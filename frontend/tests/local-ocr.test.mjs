import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { createServer } from 'vite'

let vite
let canExtractLocally
before(async () => {
  vite = await createServer({
    configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  })
  ;({ canExtractLocally } = await vite.ssrLoadModule('/src/localOcr.ts'))
})
after(async () => await vite?.close())

test('local OCR is offered only for supported formats within the 8 MB limit', () => {
  assert.equal(canExtractLocally('notice.PNG', 1024, true), true)
  assert.equal(canExtractLocally('notice.pdf', 8 * 1024 * 1024, true), true)
  assert.equal(canExtractLocally('notice.pdf', 8 * 1024 * 1024 + 1, true), false)
  assert.equal(canExtractLocally('notice.docx', 1024, true), false)
  assert.equal(canExtractLocally('notice.docx', 1024, false, true), true)
  assert.equal(canExtractLocally('notice.docx', 8 * 1024 * 1024 + 1, false, true), false)
  assert.equal(canExtractLocally('notice.png', 1024, false, true), false)
  assert.equal(canExtractLocally('notice.png', 1024, false), false)
})
