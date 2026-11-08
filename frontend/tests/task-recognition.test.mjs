import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { createSSRApp, h } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { renderToString } from '@vue/server-renderer'
import { createServer } from 'vite'

let vite, TaskList
before(async () => {
  vite = await createServer({
    configFile: false,
    plugins: [vue()],
    resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  })
  ;({ default: TaskList } = await vite.ssrLoadModule('/src/components/TaskList.vue'))
})
after(async () => await vite?.close())

async function render(canRecognize) {
  const tasks = ['todo', 'completed', 'cancelled', 'archived'].map((status) => ({
    id: status,
    title: status,
    status,
    course: '',
    due_at: null,
    due_date: null,
    recognition_method: 'rule',
  }))
  const app = createSSRApp({ render: () => h(TaskList, { tasks, canRecognize }) })
  app.use(createPinia())
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/tasks/:id?', component: TaskList }],
  })
  await router.push('/tasks')
  app.use(router)
  return renderToString(app)
}

test('my tasks can recognize every task regardless of its status', async () => {
  const html = await render(true)
  for (const status of ['todo', 'completed', 'cancelled', 'archived'])
    assert.match(html, new RegExp('aria-label="重新识别：' + status + '"'))
})

test('other task lists retain their existing controls', async () => {
  assert.doesNotMatch(await render(false), /aria-label="重新识别：/)
})
