'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { createTestServer } = require('./lib/browser-test-server.cjs');

(async () => {
  const fixture = createTestServer('campus-dialogs-');
  let browser;
  try {
    const { url, api } = await fixture.start();
    const imported = await api('messages', {
      text: '《弹窗检查课程》 第一次作业：提交实验报告。截止2026年11月8日18:00。\n参考资料：' + '附件说明'.repeat(160) + '\n' + '请对照课程原文核对材料。\n'.repeat(24),
      captured_at: '2026-11-01T08:00:00+08:00',
    });
    await api('proposals/' + imported.proposals[0].id + '/confirm', {});
    const task = (await api('tasks'))[0];
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    const page = await browser.newPage();
    page.setDefaultTimeout(10000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));

    async function checkDialog(frame, id) {
      const dialog = frame.locator('#' + id + '[open]');
      await dialog.waitFor();
      await dialog.evaluate(el => Promise.all(el.getAnimations().map(animation => animation.finished.catch(() => {}))));
      assert.equal(await frame.locator('dialog[open]').count(), 1, id + ': overlapping dialogs');
      assert.equal(await dialog.locator('.close-button:visible').count(), 1, id + ': close button');
      assert.equal(await page.getByRole('button', { name: '关闭操作面板', exact: true }).count(), 0);
      assert(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1), id + ': horizontal overflow');
      if (await page.locator('.ci-panel-frame').count()) {
        assert(await dialog.evaluate(el => {
          const rect = el.getBoundingClientRect();
          return Math.abs(rect.left) <= 1 && Math.abs(rect.top) <= 1 &&
            Math.abs(rect.width - innerWidth) <= 1 && Math.abs(rect.height - innerHeight) <= 1;
        }), id + ': dialog must fill its panel without an inner backdrop');
      }
    }

    async function closePanel(frame, id) {
      await checkDialog(frame, id);
      await frame.locator('#' + id + ' .close-button').click();
      await page.locator('.ci-panel-frame').waitFor({ state: 'detached' });
      assert.equal(await page.locator('.ci-modal-shade').count(), 0, 'leftover overlay');
    }

    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: width === 320 ? 650 : 1000 });
      await page.goto(url);
      const nav = page.getByRole('navigation', { name: '主导航' });
      assert.equal(await nav.getByRole('button').count(), 6);
      assert.equal(await nav.getByRole('button', { name: /学习通同步|手机连接|识别评测/ }).count(), 0);
      for (const name of ['今天', '我的任务', '日历', '收件箱', '提醒', '设置']) {
        await nav.getByRole('button', { name: new RegExp(name) }).click();
        const workspaceView = { '收件箱': 'inbox' }[name];
        if (workspaceView) {
          await page.frameLocator('.ci-workspace-frame').locator('#content[data-page=' + workspaceView + ']').waitFor();
        }
        assert.equal(await page.getByRole('button', { name: '＋ 新建任务', exact: true }).count(), name === '我的任务' ? 1 : 0);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), name + ': horizontal overflow');
      }
      assert.equal(await page.getByRole('button', { name: /识别评测/ }).count(), 0);
      for (const [name, view] of [['学习通同步', 'sync'], ['手机连接', 'mobile']]) {
        await page.getByRole('button', { name, exact: true }).click();
        await page.frameLocator('.ci-workspace-frame').locator('#content[data-page=' + view + ']').waitFor();
        assert.equal(await nav.getByRole('button', { name: /设置/ }).getAttribute('aria-current'), 'page');
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), name + ': horizontal overflow');
        await page.getByRole('button', { name: '← 返回设置', exact: true }).click();
        await page.getByRole('heading', { name: '🔌 数据源连接', exact: true }).waitFor();
      }
      await page.goto(url + '#evaluation');
      await page.getByRole('heading', { name: '嗨，同学！开个好头 ✌️', exact: true }).waitFor();

      await nav.getByRole('button', { name: /我的任务/ }).click();
      await page.getByRole('button', { name: '＋ 新建任务', exact: true }).click();
      const createDialog = page.getByRole('dialog', { name: '✨ 新建任务', exact: true });
      await createDialog.waitFor();
      assert(await createDialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1), 'new task overflow');
      await createDialog.getByRole('button', { name: '关闭新建任务', exact: true }).click();
      await createDialog.waitFor({ state: 'detached' });
      await page.getByRole('button', { name: '✦ 课务助手', exact: true }).click();
      await closePanel(page.frameLocator('.ci-panel-frame'), 'assistantDialog');

      await page.getByRole('button', { name: task.title, exact: true }).click();
      let frame = page.frameLocator('.ci-panel-frame');
      await checkDialog(frame, 'taskDialog');
      await frame.locator('#taskDetail [data-supplement]').click();
      await closePanel(frame, 'supplementDialog');

      await page.goto(url + '?message=' + imported.message.id);
      frame = page.frameLocator('.ci-panel-frame');
      await checkDialog(frame, 'messageDialog');
      await frame.locator('#messageDetail [data-detail]').first().click();
      await closePanel(frame, 'taskDialog');

      await page.goto(url + '?task=' + task.id);
      frame = page.frameLocator('.ci-panel-frame');
      await checkDialog(frame, 'taskDialog');
      await frame.locator('#detailStatus').selectOption('submitted');
      await frame.locator('#statusConfirmDialog[open]').waitFor();
      const confirmation = frame.locator('#statusConfirmDialog');
      await confirmation.evaluate(el => Promise.all(el.getAnimations().map(animation => animation.finished.catch(() => {}))));
      assert(await confirmation.evaluate(el => {
        const rect = el.getBoundingClientRect();
        return rect.height < innerHeight - 16 && Math.abs(rect.top - (innerHeight - rect.height) / 2) <= 1;
      }), 'nested confirmation should remain compact and centered');
      assert.equal(await frame.locator('#statusConfirmDialog .close-button:visible').count(), 1);
      await frame.locator('#statusConfirmDialog .close-button').click();
      await frame.locator('#statusConfirmDialog').waitFor({ state: 'hidden' });
      await checkDialog(frame, 'taskDialog');
      await frame.locator('#detailStatus').press('Escape');
      await page.locator('.ci-panel-frame').waitFor({ state: 'detached' });
      assert.equal(await page.locator('.ci-modal-shade').count(), 0);

      await nav.getByRole('button', { name: /收件箱/ }).click();
      frame = page.frameLocator('.ci-workspace-frame');
      await frame.locator('#content[data-page=inbox]').waitFor();
      await frame.locator('[data-inbox-tab="messages"]').click();
      await frame.locator('[data-message-detail="' + imported.message.id + '"]').click();
      await checkDialog(frame, 'messageDialog');
      await frame.locator('#messageDialog .close-button').click();
      await frame.locator('#messageDialog').waitFor({ state: 'hidden' });
      await frame.locator('#selectPageMessages').check();
      await frame.locator('#clearSelectedMessages').click();
      await frame.locator('#cleanupDialog[open]').waitFor();
      assert(await frame.locator('#cleanupDialog').evaluate(el => el.scrollWidth <= el.clientWidth + 1), 'cleanup overflow');
      await frame.locator('#cleanupCancel').click();
      await frame.locator('#cleanupDialog').waitFor({ state: 'hidden' });
      await frame.locator('#content[data-page=inbox]').waitFor();
      assert.equal(await page.locator('.ci-workspace-frame').count(), 1);
      console.log(width + 'px: six navigation pages, settings connections, removed evaluation, import and dialogs verified');
    }
    assert.deepEqual(errors, []);
  } finally {
    if (browser) await browser.close();
    await fixture.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
