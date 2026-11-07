import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import test from 'node:test'
const require = createRequire(import.meta.url)
const {
  browserOptions,
  launchBrowser,
} = require('../../integrations/chaoxing/desktop-sync/browser.cjs')

test('Windows preserves Edge, Mac selects an installed dedicated Chrome', () => {
  assert.deepEqual(browserOptions({ platform: 'win32', headless: false }), {
    channel: 'msedge',
    headless: false,
  })
  const result = browserOptions({
    platform: 'darwin',
    exists: (p) => p.includes('Google Chrome'),
    headless: false,
  })
  assert.equal(
    result.executablePath,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  )
  assert.equal(result.headless, false)
  assert.equal(result.userDataDir, undefined)
})

test('Mac can use Edge or installed Chromium; missing browsers fail clearly', () => {
  assert(
    browserOptions({
      platform: 'darwin',
      exists: (p) => p.includes('Microsoft Edge'),
    }).executablePath.includes('Microsoft Edge'),
  )
  assert.equal(
    browserOptions({
      platform: 'darwin',
      exists: (p) => p === '/synthetic/chromium',
      chromiumPath: '/synthetic/chromium',
    }).executablePath,
    '/synthetic/chromium',
  )
  assert.throws(
    () => browserOptions({ platform: 'darwin', exists: () => false }),
    (e) => e.code === 'CAMPUSINBOX_BROWSER_MISSING',
  )
})

test('launch does not reuse a personal browser profile or pass session cookies as arguments', async () => {
  let options
  await launchBrowser({
    executablePath: () => process.execPath,
    launch: async (o) => {
      options = o
      return {}
    },
  })
  assert.equal(options.headless, true)
  assert.equal(options.args, undefined)
  assert.equal(options.userDataDir, undefined)
})

test('course portals accept both official templates and reject unrelated origins', () => {
  const { coursePortal } = require('../../integrations/chaoxing/desktop-sync/task-center.cjs')
  assert(coursePortal('https://mooc2-ans.chaoxing.com/visit/interaction'))
  assert(coursePortal('https://mooc1-1.chaoxing.com/visit/interaction?synthetic=1'))
  for (const url of [
    'http://mooc1-1.chaoxing.com/visit/interaction',
    'https://mooc1-1.chaoxing.com.evil.test/visit/interaction',
    'https://mooc1-1.chaoxing.com/login',
    'https://mooc1-1.chaoxing.com:8443/visit/interaction',
    'https://user@mooc1-1.chaoxing.com/visit/interaction',
    'not-a-url',
  ])
    assert.equal(coursePortal(url), false)
})

test('course notice window includes the boundary and leaves unknown dates for review', () => {
  const {
    withinNoticeWindow,
    NOTICE_DAYS,
  } = require('../../integrations/chaoxing/desktop-sync/scope.cjs')
  const current = Date.parse('2026-11-05T10:00:00Z')
  assert.equal(NOTICE_DAYS, 90)
  assert(withinNoticeWindow('2026-08-07T10:00:00Z', current))
  assert.equal(withinNoticeWindow('2026-08-07T09:59:59Z', current), false)
  assert(withinNoticeWindow(null, current))
  assert(withinNoticeWindow('unknown', current))
})

test('date-only older notice labels are filtered before fetching details', () => {
  const { noticeListInWindow } = require('../../integrations/chaoxing/desktop-sync/scope.cjs')
  const reference = Date.parse('2026-11-05T10:00:00+08:00')
  assert.equal(noticeListInWindow('2025-10-01', reference), false)
  assert.equal(noticeListInWindow('2026年08月06日', reference), false)
  assert(noticeListInWindow('2026-10-06', reference))
  assert(noticeListInWindow('2026-11-04 16:00', reference))
  assert(noticeListInWindow('', reference))
})
