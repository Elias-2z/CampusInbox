import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { after, afterEach, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { createServer } from 'vite'

let vite
let createChaoxingConnection
const originalWindow = globalThis.window
const originalFetch = globalThis.fetch
const previousTimezone = process.env.TZ

before(async () => {
  process.env.TZ = 'Asia/Shanghai'
  vite = await createServer({
    configFile: false,
    plugins: [vue()],
    resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  })
  if (existsSync(new URL('../src/chaoxing.ts', import.meta.url))) {
    ;({ createChaoxingConnection } = await vite.ssrLoadModule('/src/chaoxing.ts'))
  }
})
after(async () => {
  await vite?.close()
  if (previousTimezone === undefined) delete process.env.TZ
  else process.env.TZ = previousTimezone
})
afterEach(() => {
  globalThis.window = originalWindow
  globalThis.fetch = originalFetch
})

function scenario() {
  const calls = []
  const timers = new Map()
  let nextTimer = 0
  const status = {
    enabled: false,
    state: 'idle',
    error: '',
    qr_ready: false,
    last_received: null,
    counts: { pending: 0, done: 0, failed: 1, needs_time: 1, needs_review: 1 },
  }
  let events = [
    {
      id: 'unknown',
      state: 'needs_time',
      kind: 'task_center',
      title: '作业',
      text: '明天交',
      course: '软件工程',
      display_time: '昨天',
    },
    {
      id: 'failed',
      state: 'failed',
      kind: 'chat',
      title: '',
      text: '处理失败的原文',
      course: '',
      display_time: '',
    },
    {
      id: 'review',
      state: 'needs_review',
      kind: 'course_notice',
      title: '附件',
      text: '',
      course: '',
      display_time: '',
    },
  ]
  let failAction = false
  let delayStatus = false
  let releaseStatus
  globalThis.window = {
    setTimeout,
    clearTimeout,
    setInterval(callback) {
      const id = ++nextTimer
      timers.set(id, callback)
      return id
    },
    clearInterval(id) {
      timers.delete(id)
    },
  }
  globalThis.fetch = async (path, options = {}) => {
    const method = options.method || 'GET'
    calls.push({ path, method, body: options.body && JSON.parse(options.body) })
    if (method === 'GET' && path === '/api/connectors/chaoxing') {
      if (delayStatus) {
        delayStatus = false
        const snapshot = structuredClone(status)
        return new Promise((resolve) => {
          releaseStatus = () => resolve(Response.json(snapshot))
        })
      }
      return Response.json(status)
    }
    if (method === 'GET' && path === '/api/connectors/chaoxing/events') return Response.json(events)
    if (failAction) return Response.json({ detail: '连接失败，请检查环境。' }, { status: 503 })
    if (path === '/api/connectors/chaoxing/connect')
      Object.assign(status, { enabled: true, state: 'connecting' })
    if (path === '/api/connectors/chaoxing/pause')
      Object.assign(status, { enabled: false, state: 'paused' })
    if (path === '/api/connectors/chaoxing/disconnect')
      Object.assign(status, { enabled: false, state: 'idle' })
    if (path.endsWith('/resolve') || path.endsWith('/retry')) {
      const id = path.split('/').at(-2)
      if (id !== 'review') events = events.filter((event) => event.id !== id)
      return Response.json({ state: id === 'review' ? 'needs_review' : 'done' })
    }
    return Response.json(status)
  }
  return {
    calls,
    status,
    delayNextStatus() {
      delayStatus = true
    },
    releaseStatus() {
      releaseStatus()
    },
    failNextActions() {
      failAction = true
    },
    async tick() {
      await Promise.all([...timers.values()].map((callback) => callback()))
    },
  }
}

test('loading and polling do not connect automatically and stop on disposal', async () => {
  assert.equal(typeof createChaoxingConnection, 'function')
  const api = scenario()
  const connection = createChaoxingConnection(() => {})
  await connection.start()
  assert.equal(connection.status.value.state, 'idle')
  assert.deepEqual(
    connection.events.value.map((event) => event.state),
    ['needs_time', 'failed', 'needs_review'],
  )
  assert.equal(api.calls.filter((call) => call.method === 'POST').length, 0)
  await api.tick()
  assert.equal(api.calls.filter((call) => call.path === '/api/connectors/chaoxing').length, 2)
  connection.stop()
  await api.tick()
  assert.equal(api.calls.filter((call) => call.path === '/api/connectors/chaoxing').length, 2)
})

test('a delayed poll cannot replace a newer action result', async () => {
  assert.equal(typeof createChaoxingConnection, 'function')
  const api = scenario()
  api.delayNextStatus()
  const connection = createChaoxingConnection(() => {})
  const firstRead = connection.start()
  await connection.perform('connect', { visible: false })
  assert.equal(connection.status.value.state, 'connecting')
  api.releaseStatus()
  await firstRead
  assert.equal(connection.status.value.state, 'connecting')
  connection.stop()
})

test('explicit actions send the chosen mode and surface failures', async () => {
  assert.equal(typeof createChaoxingConnection, 'function')
  const api = scenario()
  const connection = createChaoxingConnection(() => {})
  await connection.start()
  await connection.perform('connect', { visible: false })
  assert.equal(connection.status.value.state, 'connecting')
  await connection.perform('connect', { visible: true })
  await connection.perform('recheck')
  await connection.perform('pause')
  await connection.perform('disconnect')
  assert.deepEqual(
    api.calls.filter((call) => call.method === 'POST').map(({ path, body }) => [path, body]),
    [
      ['/api/connectors/chaoxing/connect', { visible: false }],
      ['/api/connectors/chaoxing/connect', { visible: true }],
      ['/api/connectors/chaoxing/recheck', undefined],
      ['/api/connectors/chaoxing/pause', undefined],
      ['/api/connectors/chaoxing/disconnect', undefined],
    ],
  )
  api.failNextActions()
  await connection.perform('connect', { visible: false })
  assert.match(connection.error.value, /连接失败/)
  await api.tick()
  assert.match(connection.error.value, /连接失败/)
  connection.stop()
})

test('unknown time is never guessed and the supplied local time is sent with a timezone', async () => {
  assert.equal(typeof createChaoxingConnection, 'function')
  const api = scenario()
  let imported = 0
  const connection = createChaoxingConnection(() => {
    imported++
  })
  await connection.start()
  await connection.resolve('unknown', '')
  assert.match(connection.error.value, /真实发布时间/)
  assert.equal(api.calls.filter((call) => call.method === 'POST').length, 0)
  await connection.resolve('unknown', '2026-11-03T09:00')
  const posted = api.calls.find((call) => call.path.endsWith('/resolve'))
  assert.deepEqual(posted.body, { sent_at: '2026-11-03T01:00:00.000Z' })
  assert.equal(
    connection.events.value.some((event) => event.id === 'unknown'),
    false,
  )
  assert.equal(imported, 1)
  await connection.retry('failed')
  await connection.retry('review')
  assert.equal(
    connection.events.value.some((event) => event.id === 'failed'),
    false,
  )
  assert.equal(connection.events.value.find((event) => event.id === 'review').state, 'needs_review')
  assert.equal(imported, 2)
  connection.stop()
})

test('original retrieval is explicit and uses the root connector route', async () => {
  const api = scenario()
  const connection = createChaoxingConnection(() => {})
  await connection.start()
  assert.equal(
    api.calls.some((call) => call.path.endsWith('/fetch')),
    false,
  )
  await connection.fetchOriginal('event id', 2)
  const request = api.calls.find((call) => call.path.endsWith('/fetch'))
  assert.equal(request.path, '/api/connectors/chaoxing/events/event%20id/attachments/2/fetch')
  assert.equal(request.method, 'POST')
  connection.stop()
})
