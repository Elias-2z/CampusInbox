import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createServer } from 'vite'
let vite, Badge
before(async () => {
  vite = await createServer({
    configFile: false,
    plugins: [vue()],
    resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  })
  ;({ default: Badge } = await vite.ssrLoadModule('/src/components/RecognitionBadge.vue'))
})
after(async () => {
  await vite?.close()
})
async function render(props) {
  return renderToString(createSSRApp({ render: () => h(Badge, props) }))
}
test('rule and mixed records display their actual paths instead of inventing pure model labels', async () => {
  const rule = await render({ method: 'rule' })
  assert.match(rule, /规则识别：查看识别说明/)
  assert.match(rule, /未采用模型补充字段/)
  assert.doesNotMatch(rule, /<b>模型补充<\/b>/)
  const mixed = await render({ method: 'mixed' })
  assert.match(mixed, /规则＋模型：查看识别说明/)
  assert.match(mixed, /<b>模型补充<\/b>/)
  assert.match(mixed, /<b>规则提取<\/b>/)
})
test('manual and unknown records do not claim model recognition', async () => {
  assert.match(await render({ method: 'manual' }), /由用户手动创建/)
  assert.match(await render({ method: '' }), /未保存明确的识别方式/)
})
test('field provenance distinguishes date arithmetic and user edits and escapes original text', async () => {
  const html = await render({
    method: 'mixed',
    evidence: {
      due_date: { method: 'llm+date', quote: '本月底' },
      title: {
        method: 'user',
        quote: '用户编辑',
        original: { method: 'llm', quote: '<img src=x onerror=alert(1)>' },
      },
    },
  })
  assert.match(html, /模型提取＋日期运算/)
  assert.match(html, /用户确认 \/ 修改/)
  assert.match(html, /原识别：模型提取/)
  assert.doesNotMatch(html, /<img src=x/)
  assert.match(html, /&lt;img/)
})
