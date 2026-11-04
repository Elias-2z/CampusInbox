import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

let vite, reminders
before(async () => {
  vite = await createServer({
    configFile: false,
    resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  })
  reminders = await vite.ssrLoadModule('/src/browserReminders.ts')
})
after(async () => {
  await vite?.close()
})

function setup(permission = 'granted') {
  const data = new Map()
  const storage = {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => data.set(key, value),
    removeItem: (key) => data.delete(key),
  }
  const sent = []
  class Notifications {
    static permission = permission
    static async requestPermission() {
      return this.permission
    }
    constructor(title, options) {
      sent.push({ title, ...options })
    }
  }
  return { storage, sent, Notifications }
}
const item = (id, version = 0, status = 'unread') => ({
  id,
  version,
  status,
  phase: 'upcoming',
  task: { title: id, course: '测试课程' },
  due_at: '2026-11-04T10:00:00+08:00',
  due_date: null,
})

test('enabling ignores existing unread and sends each new unread occurrence once', async () => {
  const { storage, sent, Notifications } = setup()
  assert.equal(await reminders.enableBrowserReminders(storage, Notifications, [item('old')]), true)
  reminders.dispatchBrowserReminders(storage, Notifications, [
    item('old'),
    item('new'),
    item('read', 0, 'read'),
  ])
  reminders.dispatchBrowserReminders(storage, Notifications, [item('old'), item('new')])
  assert.deepEqual(
    sent.map((n) => n.title),
    ['new'],
  )
  reminders.dispatchBrowserReminders(storage, Notifications, [item('new', 1, 'snoozed')])
  reminders.dispatchBrowserReminders(storage, Notifications, [item('new', 1)])
  assert.deepEqual(
    sent.map((n) => n.title),
    ['new', 'new'],
  )
  reminders.disableBrowserReminders(storage)
  reminders.dispatchBrowserReminders(storage, Notifications, [item('third')])
  assert.equal(sent.length, 2)
})

test('denied permission does not enable or send notifications', async () => {
  const { storage, sent, Notifications } = setup('denied')
  assert.equal(await reminders.enableBrowserReminders(storage, Notifications, []), false)
  reminders.dispatchBrowserReminders(storage, Notifications, [item('new')])
  assert.equal(sent.length, 0)
  assert.equal(reminders.browserRemindersEnabled(storage), false)
})
