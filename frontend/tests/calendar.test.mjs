import assert from 'node:assert/strict'
import { test } from 'node:test'
import { addDays, dayKey, days, parseDay, range, shiftMonth, weekStart } from '../src/calendar.ts'

for (const timezone of ['Asia/Shanghai', 'America/New_York', 'UTC']) {
  test(`calendar navigation and boundaries in ${timezone}`, () => {
    const previous = process.env.TZ
    process.env.TZ = timezone
    try {
      assert.equal(dayKey(parseDay('2026-10-31')), '2026-10-31')
      assert.equal(parseDay('2026-02-29'), null)
      assert.equal(parseDay('2024-02-29')?.getDate(), 29)
      assert.equal(parseDay('2026-13-01'), null)
      assert.equal(parseDay('2026-10-31T12:00:00Z'), null)
      assert.equal(parseDay(['2026-10-31']), null)
      assert.equal(dayKey(shiftMonth(parseDay('2026-01-31'), 1)), '2026-02-28')
      assert.equal(dayKey(shiftMonth(parseDay('2024-01-31'), 1)), '2024-02-29')
      assert.equal(dayKey(shiftMonth(parseDay('2026-12-31'), 1)), '2027-01-31')
      assert.equal(dayKey(weekStart(parseDay('2026-11-01'))), '2026-10-26')
      assert.deepEqual(range(parseDay('2026-10-31'), 'month'), {
        start: '2026-10-01',
        end: '2026-11-01',
      })
      assert.deepEqual(range(parseDay('2026-11-01'), 'week'), {
        start: '2026-10-26',
        end: '2026-11-02',
      })
      const month = days(parseDay('2026-02-15'), 'month')
      assert.equal(month.length, 42)
      assert.equal(month[0].getDay(), 1)
      assert.equal(new Set(month.map(dayKey)).size, 42)
      assert.equal(dayKey(month[0]), '2026-01-26')
      assert.equal(dayKey(month.at(-1)), '2026-03-08')
      assert.equal(days(parseDay('2026-12-31'), 'week').length, 7)
      assert.equal(dayKey(addDays(parseDay('2026-03-07'), 2)), '2026-03-09')
      assert.equal(dayKey(addDays(parseDay('2026-10-31'), 2)), '2026-11-02')
      assert.equal(addDays(parseDay('2026-03-07'), 2).getHours(), 12)
      assert.equal(
        dayKey(new Date('2026-10-31T00:30:00+08:00')),
        timezone === 'Asia/Shanghai' ? '2026-10-31' : '2026-10-30',
      )
    } finally {
      if (previous === undefined) delete process.env.TZ
      else process.env.TZ = previous
    }
  })
}
