import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { createServer } from 'vite'
import { createSSRApp } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { renderToString } from '@vue/server-renderer'

let vite
let InboxView
let useCampus
before(async () => {
  vite = await createServer({
    configFile: false,
    plugins: [vue()],
    resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  })
  ;({ default: InboxView } = await vite.ssrLoadModule('/src/views/InboxView.vue'))
  ;({ useCampus } = await vite.ssrLoadModule('/src/stores/campus.ts'))
})
after(async () => await vite?.close())

async function render(
  context,
  status = 'processing',
  activitySchedule = [],
  classification = null,
  raw = false,
) {
  const pinia = createPinia()
  const store = useCampus(pinia)
  store.ready = true
  store.inbox = [
    {
      id: 'suggestion',
      status,
      action: 'create',
      method: 'rule',
      fields: {},
      evidence: {},
      warnings: [],
      classification,
      activity_schedule: activitySchedule,
      message: {
        id: 'message',
        text: '一条通知',
        source: '来源',
        sent_at: '2026-11-03T01:00:00Z',
        timezone: 'Asia/Shanghai',
        received_at: '2026-11-03T01:00:00Z',
        attachments: [],
        source_context: context,
      },
      matched_tasks: [],
      task_id: null,
      updated_at: '2026-11-03T01:00:00Z',
      merge_change: null,
      cancellation_supported: false,
      cancellation_change: null,
      split_supported: false,
      split_items: [],
      deadline_supported: false,
      deadline_change: null,
    },
  ]
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/inbox', component: InboxView }],
  })
  await router.push('/inbox?id=suggestion')
  await router.isReady()
  const app = createSSRApp(InboxView)
  app.use(pinia)
  app.use(router)
  const html = await renderToString(app)
  return raw ? html : html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ')
}

test('a routed chat opens in diversion with its quote and recovery control', async () => {
  const html = await render(
    null,
    'ignored',
    [],
    { decision: 'chat', method: 'rule', quote: '好的，谢谢！', note: '完整消息为简短回复' },
    true,
  )
  assert.match(html, /aria-pressed="true"[^>]*>\s*聊天分流/)
  assert.match(html, /分类依据：好的，谢谢！/)
  assert.match(html, /恢复为待处理/)
  assert.doesNotMatch(html, /确认创建任务/)
})

test('a restored chat opens in pending rather than diversion', async () => {
  const html = await render(null, 'pending', [], { decision: 'chat', note: '', quote: '' }, true)
  assert.match(html, /aria-pressed="true"[^>]*>\s*待处理/)
})

test('learning-platform source shows its own fields without QQ labels', async () => {
  const text = await render({
    platform: 'chaoxing',
    course: '软件工程',
    group_name: '课程会话',
    sender_name: '任课教师',
    message_id: 'cx-123',
    kind: 'course_notice',
    event_id: 'local-event',
    missing_originals: [
      { index: 0, name: '要求.txt' },
      { index: 1, name: '讲义.pdf', recoverable: true },
    ],
  })
  assert.match(text, /课程：\s*软件工程/)
  assert.match(text, /会话：\s*课程会话/)
  assert.match(text, /发件人：\s*任课教师/)
  assert.match(text, /原消息编号：\s*cx-123/)
  assert.match(text, /补下载原件 · 要求\.txt/)
  assert.match(text, /恢复原件 · 讲义\.pdf/)
  assert.doesNotMatch(text, /群号|发件人 QQ/)
})

test('archived notices show their history and can be restored', async () => {
  const text = await render(null, 'archived')
  assert.match(text, /历史归档/)
  assert.match(text, /恢复为待处理/)
  assert.doesNotMatch(text, /确认创建任务/)
})

test('an experimental schedule is displayed with its original quote', async () => {
  const text = await render({ platform: 'chaoxing', kind: 'course_notice' }, 'pending', [
    '实验时间：11月2日18:00',
  ])
  assert.match(text, /原文中的实验安排.*实验时间：11月2日18:00/)
})

test('closed platform work stays visible in history without a restore action', async () => {
  const text = await render(
    { platform: 'chaoxing', kind: 'task_center', closed_task_status: true },
    'archived',
  )
  assert.match(text, /历史归档/)
  assert.match(text, /一条通知/)
  assert.doesNotMatch(text, /恢复为待处理/)
})

test('QQ source keeps its group number and sender QQ fields', async () => {
  const text = await render({
    platform: 'qq',
    course: '软件工程',
    group_name: '课程群',
    sender_name: '教师',
    group_id: '123456',
    sender_id: '789012',
  })
  assert.match(text, /原群名：\s*课程群/)
  assert.match(text, /群号：\s*123456/)
  assert.match(text, /发件人 QQ：\s*789012/)
  assert.doesNotMatch(text, /补下载原件/)
})

test('migrated unknown publication time is explicit and never displayed as epoch', async () => {
  const text = await render(
    {
      platform: 'legacy',
      namespace: 'my-old',
      legacy_id: 'old-123',
      time_known: false,
      course: '',
      group_name: '',
    },
    'pending',
  )
  assert.match(text, /发布时间未知/)
  assert.match(text, /old-123/)
  assert.doesNotMatch(text, /1970|发送于 11\/3/)
})
