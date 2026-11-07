import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import test from 'node:test'

const require = createRequire(import.meta.url)
const {
  withinNoticeWindow,
  noticeListInWindow,
} = require('../../integrations/chaoxing/desktop-sync/scope.cjs')
const reference = Date.parse('2026-11-06T08:00:00Z')

test('collector includes older notices within 90 days but excludes very old ones', () => {
  assert.equal(withinNoticeWindow('2026-09-06T08:00:00Z', reference), true)
  assert.equal(withinNoticeWindow('2026-06-06T08:00:00Z', reference), false)
  assert.equal(noticeListInWindow('2026-09-06', reference), true)
  assert.equal(noticeListInWindow('2026-06-06', reference), false)
})

test('missing publication time remains eligible for explicit review', () => {
  assert.equal(withinNoticeWindow(null, reference), true)
  assert.equal(noticeListInWindow('时间未知', reference), true)
})
