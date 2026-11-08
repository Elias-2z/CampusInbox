import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { createSSRApp, h } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { renderToString } from '@vue/server-renderer'
import { createServer } from 'vite'

let vite, MergeReview, useCampus, DeadlineReview, CancellationReview
before(async () => {
  vite = await createServer({
    configFile: false,
    plugins: [vue()],
    resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  })
  ;({ default: MergeReview } = await vite.ssrLoadModule('/src/components/MergeReview.vue'))
  ;({ default: DeadlineReview } = await vite.ssrLoadModule('/src/components/DeadlineReview.vue'))
  ;({ default: CancellationReview } = await vite.ssrLoadModule(
    '/src/components/CancellationReview.vue',
  ))
  ;({ useCampus } = await vite.ssrLoadModule('/src/stores/campus.ts'))
})

for (const operation of ['merge', 'deadline', 'cancel']) {
  test(`${operation} hides cross-course, mismatched kind and different assignment numbers`, async () => {
    const tasks = [
      { id: 'valid', course: '数据库', title: '第二次作业', kind: 'homework', status: 'todo' },
      {
        id: 'wrong-course',
        course: '线性代数',
        title: '第二次作业',
        kind: 'homework',
        status: 'todo',
      },
      {
        id: 'wrong-number',
        course: '数据库',
        title: '第一次作业',
        kind: 'homework',
        status: 'todo',
      },
      { id: 'wrong-kind', course: '数据库', title: '第二次作业', kind: 'exam', status: 'todo' },
    ]
    const pinia = createPinia()
    useCampus(pinia).tasks = tasks
    const component =
      operation === 'merge'
        ? MergeReview
        : operation === 'deadline'
          ? DeadlineReview
          : CancellationReview
    const source = {
      id: 'source',
      updated_at: '1',
      fields: { title: '第二次作业', course: '数据库', kind: 'homework' },
      evidence: {},
      matched_tasks: tasks,
    }
    const app = createSSRApp({
      render: () => h(component, operation === 'merge' ? { source } : { item: source }),
    })
    app.use(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component }],
    })
    await router.push('/')
    app.use(router)
    const html = await renderToString(app)
    assert.match(html, /value="valid"/)
    assert.doesNotMatch(html, /value="wrong-(?:course|number|kind)"/)
  })
}
after(async () => await vite?.close())

test('association selector excludes other courses even from stale suggested matches', async () => {
  const tasks = [
    { id: 'same', course: '数据库', title: '第一次作业', status: 'todo' },
    { id: 'other', course: '线性代数', title: '第一次作业', status: 'todo' },
    { id: 'alias', course: '数据库原理', title: '第一次作业', status: 'todo' },
  ]
  const pinia = createPinia()
  useCampus(pinia).tasks = tasks
  const app = createSSRApp({
    render: () =>
      h(MergeReview, {
        source: {
          id: 'notice',
          updated_at: '1',
          fields: { course: '数据库' },
          evidence: {},
          matched_tasks: tasks,
        },
      }),
  })
  app.use(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: MergeReview }],
  })
  await router.push('/')
  app.use(router)
  const html = await renderToString(app)
  assert.match(html, /value="same"/)
  assert.doesNotMatch(html, /value="other"|value="alias"/)
})
