import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { createSSRApp } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { renderToString } from '@vue/server-renderer'
import { createServer } from 'vite'

let vite, ConnectionDetailView, ConnectionsView
before(async () => {
  vite = await createServer({
    configFile: false,
    plugins: [vue()],
    resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  })
  ;({ default: ConnectionDetailView } = await vite.ssrLoadModule(
    '/src/views/ConnectionDetailView.vue',
  ))
  ;({ default: ConnectionsView } = await vite.ssrLoadModule('/src/views/ConnectionsView.vue'))
})

test('connection overview offers learning platform management', async () => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/settings/connections', component: ConnectionsView },
      { path: '/settings/connections/:provider', component: ConnectionDetailView },
      { path: '/settings', component: ConnectionsView },
    ],
  })
  await router.push('/settings/connections')
  await router.isReady()
  const app = createSSRApp(ConnectionsView)
  app.use(createPinia())
  app.use(router)
  const html = await renderToString(app)
  assert.match(html, /管理学习通连接/)
})
after(async () => await vite?.close())

test('learning platform detail exposes the actual connector instead of a placeholder', async () => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/settings/connections/:provider', component: ConnectionDetailView }],
  })
  await router.push('/settings/connections/chaoxing')
  await router.isReady()
  const app = createSSRApp(ConnectionDetailView)
  app.use(createPinia())
  app.use(router)
  const html = await renderToString(app)
  assert.match(html, /扫码连接/)
  assert.doesNotMatch(html, /暂未接入主应用/)
})
