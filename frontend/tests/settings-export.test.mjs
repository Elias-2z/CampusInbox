import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

const module = await import('../src/settingsExport.ts').catch((error) => {
  if (error.code === 'ERR_MODULE_NOT_FOUND') return {}
  throw error
})
const original = {
  fetch: globalThis.fetch,
  document: globalThis.document,
  window: globalThis.window,
  createObjectURL: URL.createObjectURL,
  revokeObjectURL: URL.revokeObjectURL,
}

afterEach(() => {
  globalThis.fetch = original.fetch
  globalThis.document = original.document
  globalThis.window = original.window
  URL.createObjectURL = original.createObjectURL
  URL.revokeObjectURL = original.revokeObjectURL
})

test('JSON export downloads the returned file', async () => {
  assert.equal(typeof module.downloadData, 'function')
  let requested
  let clicked = false
  let removed = false
  let content
  const link = {
    click() {
      clicked = true
    },
    remove() {
      removed = true
    },
  }
  globalThis.fetch = async (path) => {
    requested = path
    return new Response('{"records":{}}', { headers: { 'Content-Type': 'application/json' } })
  }
  globalThis.document = { createElement: () => link, body: { append() {} } }
  globalThis.window = {
    setTimeout(callback, delay) {
      return delay === 60000 ? (callback(), 0) : setTimeout(callback, delay)
    },
    clearTimeout,
  }
  URL.createObjectURL = (blob) => {
    content = blob
    return 'blob:test'
  }
  URL.revokeObjectURL = () => {}
  await module.downloadData('json')
  assert.equal(requested, '/api/export.json')
  assert.equal(link.download, 'campusinbox-data.json')
  assert.equal(await content.text(), '{"records":{}}')
  assert.equal(clicked, true)
  assert.equal(removed, true)
})

test('export rejects server errors and HTML instead of downloading them', async () => {
  assert.equal(typeof module.downloadData, 'function')
  globalThis.window = { setTimeout, clearTimeout }
  globalThis.fetch = async () => new Response('unavailable', { status: 503 })
  await assert.rejects(module.downloadData('txt'), /导出/)
  globalThis.fetch = async () =>
    new Response('<html></html>', { headers: { 'Content-Type': 'text/html' } })
  await assert.rejects(module.downloadData('txt'), /格式/)
})
