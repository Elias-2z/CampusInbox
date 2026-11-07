import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { createSSRApp, h } from 'vue'
import { createPinia } from 'pinia'
import { renderToString } from '@vue/server-renderer'
import { createServer } from 'vite'

let vite
let TodayView
let RemindersView
let useCampus
const now = Date.parse('2026-11-01T00:00:00Z')

before(async () => {
  vite = await createServer({
    configFile: false,
    plugins: [vue()],
    resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  })
  ;({ default: TodayView } = await vite.ssrLoadModule('/src/views/TodayView.vue'))
  ;({ default: RemindersView } = await vite.ssrLoadModule('/src/views/RemindersView.vue'))
  ;({ useCampus } = await vite.ssrLoadModule('/src/stores/campus.ts'))
})
after(async () => {
  await vite?.close()
})

function task(id, status, due_at) {
  return {
    id,
    title: id,
    course: '',
    kind: 'homework',
    description: '',
    due_at,
    submission_platform: '',
    submission_method: '',
    file_requirements: '',
    location: '',
    remarks: '',
    suggestion_id: '',
    message_id: '',
    status,
    version: 1,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
  }
}
function reminder(id, due_at, phase = 'upcoming', status = 'unread') {
  return {
    id,
    task: task(id, 'todo', due_at),
    phase,
    status,
    due_at,
    read_at: null,
    snoozed_until: null,
    version: 0,
  }
}
async function render(tasks, reminders = [], component = TodayView) {
  const pinia = createPinia()
  const store = useCampus(pinia)
  store.tasks = tasks
  store.clock = now
  store.ready = true
  store.reminderFeed = {
    config: { enabled: true, lead_minutes: 1440, overdue_enabled: true },
    items: reminders,
    evaluated_at: '2026-11-01T00:00:00Z',
  }
  const app = createSSRApp(component)
  app.use(pinia)
  app.component('RouterLink', {
    props: ['to'],
    setup(_props, { slots }) {
      return () => h('a', slots.default?.())
    },
  })
  return renderToString(app)
}

test('home hides ended check-ins even before their original deadline', async () => {
  const html = await render([
    { ...task('ended-checkin', 'in_progress', '2026-11-01T02:00:00Z'), checkin_closed: true },
    task('still-open', 'todo', '2026-11-01T02:00:00Z'),
  ])
  assert.doesNotMatch(html, /ended-checkin/)
  assert.match(html, /still-open/)
})

test('home hides expired work and calculates progress from completed and remaining tasks', async () => {
  const html = await render([
    task('past-todo', 'todo', '2026-10-31T23:59:59Z'),
    task('past-progress', 'in_progress', '2026-10-31T23:59:59Z'),
    task('due-now', 'todo', '2026-11-01T00:00:00Z'),
    task('future', 'todo', '2026-11-01T02:00:00Z'),
    task('soon', 'in_progress', '2026-11-01T01:00:00Z'),
    task('undated', 'todo', null),
    task('finished', 'completed', '2026-10-01T00:00:00Z'),
    task('cancelled', 'cancelled', '2026-11-02T00:00:00Z'),
    task('archived', 'archived', '2026-11-02T00:00:00Z'),
  ])
  assert.match(html, /还有 3 件事等你/)
  assert.match(html, /3 项待办/)
  assert.match(html, /已完成 1 \/ 待办 3/)
  assert.match(html, /aria-valuenow="1"[^>]*aria-valuemax="4"/)
  assert.match(html, /width:25%/)
  assert.doesNotMatch(html, /past-todo|past-progress|due-now/)
  for (const title of ['soon', 'future', 'undated']) {
    assert.match(html, new RegExp(`aria-label="完成：${title}"`))
  }
  assert.ok(html.indexOf('完成：soon') < html.indexOf('完成：future'))
  assert.ok(html.indexOf('完成：future') < html.indexOf('完成：undated'))
})

test('home hides progress when every remaining task is overdue', async () => {
  const html = await render([task('expired', 'todo', '2026-10-31T23:59:59Z')])
  assert.match(html, /0 项待办/)
  assert.doesNotMatch(html, /role="progressbar"/)
})

test('home reminder overview omits overdue and expired upcoming reminders', async () => {
  const html = await render(
    [],
    [
      reminder('expired', '2026-10-31T23:00:00Z', 'overdue'),
      reminder('expired-stale', '2026-10-31T23:59:59Z'),
      reminder('due-now-reminder', '2026-11-01T00:00:00Z'),
      reminder('upcoming', '2026-11-01T01:00:00Z'),
      reminder('already-read', '2026-11-01T01:00:00Z', 'upcoming', 'read'),
    ],
  )
  assert.match(html, /1 条提醒，别错过/)
  assert.match(html, /upcoming/)
  assert.doesNotMatch(html, /expired|due-now-reminder|already-read/)
})

test('reminders page and unread count hide expired reminders', async () => {
  const html = await render(
    [],
    [
      reminder('expired-clock', '2026-10-31T23:00:00Z', 'overdue'),
      reminder('expired-cached', '2026-11-01T00:00:00Z'),
      { ...reminder('expired-date', null), due_date: '2026-10-31' },
      { ...reminder('today-date', null), due_date: '2026-11-01' },
    ],
    RemindersView,
  )
  assert.match(html, /未读\s*<span> · 1<\/span>/)
  assert.match(html, /today-date/)
  assert.doesNotMatch(html, /expired-clock|expired-cached|expired-date/)
})

test('date-only tasks and legacy done records stay actionable with simple completion controls', async () => {
  const html = await render(
    [
      { ...task('yesterday', 'todo', null), due_date: '2026-10-31' },
      { ...task('today-done', 'done', null), due_date: '2026-11-01' },
      { ...task('tomorrow', 'todo', null), due_date: '2026-11-02' },
      task('submitted', 'submitted', null),
    ],
    [{ ...reminder('today-reminder', null), due_date: '2026-11-01' }],
  )
  assert.match(html, /2 项待办/)
  assert.match(html, /已完成 1 \/ 待办 2/)
  assert.match(html, /2026\/11\/01（仅日期）/)
  assert.doesNotMatch(html, /标记已提交|已做完，未提交/)
  assert.match(html, /aria-label="完成：today-done"/)
  assert.match(html, /today-reminder/)
  assert.doesNotMatch(html, /yesterday/)
})
