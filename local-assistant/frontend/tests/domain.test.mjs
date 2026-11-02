import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  dayOf,
  overdue,
  activeTasks,
  calendarDays,
  reminderItems,
  formatDeadline,
  filterTasks,
  priorityTasks,
} from '../src/domain.ts'
const at = new Date('2026-11-01T08:00:00+08:00')
const task = (id, deadline, status = 'pending') => ({
  id,
  title: '报告',
  course: '软件工程',
  scope: '一班',
  deadline,
  status,
  version: 1,
  checklist: [],
})
test('date-only values stay date-only and overdue only on the next Shanghai day', () => {
  assert.equal(dayOf('2026-11-01'), '2026-11-01')
  assert.equal(dayOf('2026-10-31T17:00:00Z'), '2026-11-01')
  assert.equal(overdue(task('a', '2026-11-01'), at), false)
  assert.equal(overdue(task('a', '2026-10-31'), at), true)
  assert.match(formatDeadline('2026-11-01'), /仅日期/)
  assert.equal(formatDeadline(null), '截止时间待补充')
})
test('done remains active until submitted and unverified checkins are not ordinary todos', () => {
  const tasks = [
    task('a', null, 'done'),
    task('b', null, 'submitted'),
    task('c', null, 'cancelled'),
    { ...task('d', null), checkin_verification: 'unverified' },
  ]
  assert.deepEqual(
    activeTasks(tasks).map((t) => t.id),
    ['a'],
  )
  assert.equal(tasks[0].status, 'done')
})
test('calendar starts Monday and handles cross-year and leap months', () => {
  const days = calendarDays('2026-11-01', 'month')
  assert.equal(days.length, 42)
  assert.equal(days[0], '2026-10-26')
  assert.equal(days.at(-1), '2026-12-06')
  assert.ok(calendarDays('2024-02-15', 'month').includes('2024-02-29'))
  assert.equal(calendarDays('2027-01-01', 'week')[0], '2026-12-28')
})
test('reminders exclude overdue tasks and respect statuses and configured time window', () => {
  const tasks = [
    task('a', '2026-11-01T09:00:00+08:00'),
    task('b', '2026-11-01T09:00:00+08:00', 'submitted'),
    task('c', null),
    task('d', '2026-10-31'),
    task('e', '2026-11-01'),
    task('past-time', '2026-11-01T07:59:00+08:00'),
    task('due-now', '2026-11-01T08:00:00+08:00'),
    task('past-done', '2026-10-31', 'done'),
    task('later', '2026-11-01T11:00:00+08:00'),
  ]
  assert.deepEqual(
    reminderItems(tasks, at, 120).map((x) => x.id),
    ['e', 'a'],
  )
})
test('search and course filters preserve scope and material arrays', () => {
  const t = { ...task('a', null), checklist: [{ text: '源代码', done: true }] }
  const result = filterTasks([t], { search: '一班', course: '软件工程', status: 'all' }, at)
  assert.equal(result[0], t)
  assert.equal(t.checklist[0].done, true)
})
test('not submitted excludes overdue tasks while keeping unknown and date-only deadlines', () => {
  const tasks = [
    task('past', '2026-10-31'),
    task('past-progress', '2026-10-31', 'in_progress'),
    task('past-done', '2026-10-31', 'done'),
    task('due-now', '2026-11-01T08:00:00+08:00'),
    task('today', '2026-11-01'),
    task('future', '2026-11-02'),
    task('unknown', null),
    task('done', '2026-11-02', 'done'),
    task('submitted', '2026-10-31', 'submitted'),
    task('cancelled', '2026-11-02', 'cancelled'),
  ]
  assert.deepEqual(
    filterTasks(tasks, { status: 'active' }, at).map((t) => t.id),
    ['today', 'future', 'unknown', 'done'],
  )
  assert.deepEqual(
    filterTasks(tasks, { status: 'overdue' }, at).map((t) => t.id),
    ['past', 'past-progress', 'past-done', 'due-now'],
  )
  assert.equal(filterTasks(tasks, { status: 'all' }, at).length, tasks.length)
  assert.deepEqual(
    filterTasks(tasks, { status: 'done' }, at).map((t) => t.id),
    ['past-done', 'done'],
  )
})
test('home excludes overdue work and prioritizes unfinished work before done tasks', () => {
  const tasks = [
    task('old', '2026-10-01'),
    task('done', '2026-11-01', 'done'),
    task('unknown', null),
    task('later', '2026-11-04'),
    task('soon', '2026-11-02', 'in_progress'),
    task('today', '2026-11-01'),
    task('third', '2026-11-03'),
    task('submitted', '2026-10-01', 'submitted'),
    task('cancelled', '2026-11-01', 'cancelled'),
  ]
  const original = tasks.map((t) => t.id)
  assert.deepEqual(
    priorityTasks(tasks, at).map((t) => t.id),
    ['today', 'soon', 'third', 'later', 'unknown'],
  )
  assert.deepEqual(
    tasks.map((t) => t.id),
    original,
  )
  assert.deepEqual(
    priorityTasks([tasks[0], tasks[1], tasks[2]], at).map((t) => t.id),
    ['unknown', 'done'],
  )
  assert.deepEqual(
    priorityTasks(
      [
        task('past-done', '2026-10-31', 'done'),
        task('due-now', '2026-11-01T08:00:00+08:00'),
        task('today', '2026-11-01'),
      ],
      at,
    ).map((t) => t.id),
    ['today'],
  )
})
