import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { createSSRApp } from 'vue'
import { createPinia } from 'pinia'
import { renderToString } from '@vue/server-renderer'
import { createServer } from 'vite'

let vite, previewLegacyExport, planLegacyMigration, importLegacyMigration
before(async () => {
  vite = await createServer({
    configFile: false,
    plugins: [vue()],
    resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  })
  ;({ previewLegacyExport, planLegacyMigration, importLegacyMigration } =
    await vite.ssrLoadModule('/src/legacyPreview.ts'))
})
after(async () => await vite?.close())

test('selected JSON is sent only to the local preview endpoint', async () => {
  const file = { name: 'courseinbox-backup.json', size: 100 }
  let call
  const result = await previewLegacyExport(file, async (path, options) => {
    call = { path, options }
    return { counts: { tasks: 2 }, issues: {} }
  })
  assert.equal(call.path, '/migrations/legacy/preview')
  assert.equal(call.options.method, 'POST')
  assert.equal(call.options.body, file)
  assert.equal(result.counts.tasks, 2)
})

test('invalid and oversized selections are rejected before upload', async () => {
  let calls = 0
  const request = async () => {
    calls++
  }
  for (const file of [
    { name: 'backup.zip', size: 1 },
    { name: 'backup.json', size: 0 },
    { name: 'backup.json', size: 10 * 1024 * 1024 + 1 },
  ]) {
    await assert.rejects(previewLegacyExport(file, request))
  }
  assert.equal(calls, 0)
})

test('settings component clearly says the check does not import data', async () => {
  const { default: component } = await vite.ssrLoadModule(
    '/src/components/LegacyMigrationPreview.vue',
  )
  const html = await renderToString(createSSRApp(component).use(createPinia()))
  assert.match(html, /旧版数据迁移检查/)
  assert.match(html, /只检查，不会导入或修改数据/)
  assert.match(html, /type="file"/)
})

test('migration sends the selected file and explicit reviewed token only after confirmation', async () => {
  const file = { name: 'migration.zip', size: 100 }
  const calls = []
  const send = async (path, options) => {
    calls.push({ path, options })
    return { plan_token: 'reviewed-plan', can_import: true }
  }
  const plan = await planLegacyMigration(file, 'my-old', send)
  await importLegacyMigration(file, 'my-old', plan.plan_token, send)
  assert.equal(calls[0].path, '/migrations/legacy/plan?namespace=my-old')
  assert.equal(calls[1].path, '/migrations/legacy/import?namespace=my-old')
  assert.equal(calls[1].options.headers['X-Migration-Confirm'], 'import')
  assert.equal(calls[1].options.headers['X-Migration-Plan'], 'reviewed-plan')
  assert.equal(calls[1].options.body, file)
})

test('invalid migration namespace, missing preview and large bundles never send', async () => {
  const send = async () => assert.fail('must not send')
  const file = { name: 'migration.zip', size: 100 }
  await assert.rejects(planLegacyMigration(file, '../other', send))
  await assert.rejects(planLegacyMigration({ ...file, size: 101 * 1024 * 1024 }, 'old', send))
  await assert.rejects(importLegacyMigration(file, 'old', '', send))
})
