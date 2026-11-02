'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('playwright');
const { createTestServer } = require('./lib/browser-test-server.cjs');

(async () => {
  const fixture = createTestServer('campus-unified-import-');
  let browser;
  try {
    const { url, api, token } = await fixture.start();
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.setDefaultTimeout(10000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    // LAN HTTP does not expose randomUUID; task creation must still work there.
    await page.addInitScript(() => Object.defineProperty(crypto, 'randomUUID', { value: undefined }));
    await page.goto(url);
    const nav = page.getByRole('navigation', { name: '主导航' });
    async function open() {
      await nav.getByRole('button', { name: /我的任务/ }).click();
      await page.getByRole('button', { name: '＋ 新建任务', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: '✨ 新建任务', exact: true });
      await dialog.waitFor();
      return dialog;
    }
    let dialog = await open();
    assert.equal(await dialog.getByRole('button', { name: '关闭新建任务', exact: true }).count(), 1);
    await dialog.getByLabel('通知原文', { exact: true }).fill('《统一导入课程》 第一次作业：提交报告。截止2026年11月8日。');
    await dialog.getByLabel('消息实际发送时间').fill('2026-11-01T08:00');
    await dialog.getByRole('button', { name: '识别并前往收件箱审核 →', exact: true }).click();
    await dialog.waitFor({ state: 'detached' });
    const workspace = page.frameLocator('.ci-workspace-frame');
    await workspace.locator('.proposal-card').waitFor();
    assert.equal((await api('tasks')).length, 0);
    await workspace.locator('[data-confirm]').first().click();
    await workspace.locator('.proposal-card').waitFor({ state: 'detached' });

    dialog = await open();
    await dialog.getByRole('button', { name: /文件上传/ }).click();
    const original = Buffer.from('《附件验收课程》 第二次作业：提交实验报告。截止2026年11月9日。');
    await dialog.getByLabel('选择课程文件').setInputFiles({ name: '通知.txt', mimeType: 'text/plain', buffer: original });
    await dialog.getByRole('button', { name: '提取文本 →', exact: true }).click();
    await dialog.getByText('✅ 文本已提取，请校正后继续', { exact: true }).waitFor();
    await dialog.getByRole('button', { name: '关闭新建任务', exact: true }).click();
    dialog = await open();
    await dialog.getByRole('button', { name: /文件上传/ }).click();
    await dialog.getByText('最近上传 · 继续处理', { exact: true }).click();
    await dialog.locator('.ci-recentfile').filter({ hasText: '通知.txt' }).click();
    assert.equal(await dialog.getByLabel('提取文本（可校正）', { exact: true }).inputValue(), original.toString());
    const download = page.waitForEvent('download');
    await dialog.getByRole('button', { name: /下载原文件/ }).click();
    assert.equal((await download).suggestedFilename(), '通知.txt');
    await dialog.getByLabel('消息实际发送时间').fill('2026-11-01T08:00');
    await dialog.getByRole('button', { name: '识别并前往收件箱审核 →', exact: true }).click();
    await dialog.waitFor({ state: 'detached' });
    await workspace.locator('.proposal-card').waitFor();
    await workspace.locator('[data-confirm]').first().click();
    await workspace.locator('.proposal-card').waitFor({ state: 'detached' });
    const attachmentTask = (await api('tasks')).find(task => task.course === '附件验收课程');
    assert(attachmentTask);
    const anotherUpload = await fetch(url + '/api/imports/files?filename=' + encodeURIComponent('通知.txt'), {
      method: 'POST', headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/octet-stream' },
      body: Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), original]),
    });
    assert(anotherUpload.ok);
    const anotherFile = await anotherUpload.json();
    await api('imports/submit', { attachment_id: anotherFile.id, text: anotherFile.text, source: '附件：通知.txt', published_at: '2026-11-01T08:00:00+08:00', mode: 'default' });
    await page.goto(url + '?task=' + attachmentTask.id);
    const panel = page.frameLocator('.ci-panel-frame');
    await panel.getByRole('button', { name: '下载原附件', exact: true }).first().waitFor();
    assert.equal(await panel.getByRole('button', { name: '下载原附件', exact: true }).count(), 2, 'same-named originals must remain separately downloadable');
    const detailDownload = page.waitForEvent('download');
    await panel.getByRole('button', { name: '下载原附件', exact: true }).first().click();
    assert.equal((await detailDownload).suggestedFilename(), '通知.txt');
    await panel.locator('#taskDialog .close-button').click();
    await page.locator('.ci-panel-frame').waitFor({ state: 'detached' });

    dialog = await open();
    await dialog.getByRole('button', { name: /手动填写/ }).click();
    await dialog.getByLabel('任务名称', { exact: true }).fill('手动录入实验');
    await dialog.getByLabel('课程', { exact: true }).fill('手动课程');
    await dialog.getByLabel('截止精度').selectOption('date');
    await dialog.getByLabel('截止日期', { exact: true }).fill('2026-11-10');
    await dialog.getByLabel('任务要求（每行一项）').fill('实验报告\n源代码');
    await dialog.getByRole('button', { name: '创建任务 ✓', exact: true }).click();
    await dialog.waitFor({ state: 'detached' });
    await page.frameLocator('.ci-panel-frame').locator('#taskDialog[open]').waitFor();
    assert.equal((await api('tasks')).find(task => task.title === '手动录入实验').deadline, '2026-11-10');
    assert.equal((await api('proposals')).length, 0);
    await page.frameLocator('.ci-panel-frame').locator('#taskDialog .close-button').click();
    await page.locator('.ci-panel-frame').waitFor({ state: 'detached' });

    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      dialog = await open();
      for (const mode of ['文本粘贴', '图片上传', '文件上传', '手动填写']) {
        await dialog.getByRole('button', { name: new RegExp(mode) }).click();
        assert(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1), mode + ': horizontal overflow');
      }
      await dialog.getByRole('button', { name: /图片上传/ }).click();
      await dialog.getByLabel('选择通知截图或照片').setInputFiles({ name: 'image.png', mimeType: 'image/png', buffer: Buffer.from('test') });
      await dialog.getByText(/尚未配置 MinerU Token/).waitFor();
      assert(await dialog.getByRole('button', { name: '发送到 MinerU 并解析 →', exact: true }).isDisabled());
      await dialog.getByRole('button', { name: '关闭新建任务', exact: true }).click();
      await dialog.waitFor({ state: 'detached' });
    }
    // Exercise the hosted parsing UI with isolated fake responses, never real cloud uploads.
    let hosted = null;
    let uploaded = 0;
    let finish = false;
    await page.route('**/api/imports/**', async route => {
      const request = route.request();
      const parsed = new URL(request.url());
      if (parsed.pathname === '/api/imports/capabilities') {
        return route.fulfill({ json: { mineru_configured: true, max_bytes: 20 * 1024 * 1024, extensions: ['.png'] } });
      }
      if (parsed.pathname === '/api/imports/files' && request.method() === 'GET') {
        return route.fulfill({ json: hosted ? [hosted] : [] });
      }
      if (parsed.pathname === '/api/imports/files' && request.method() === 'POST') {
        assert.equal(parsed.searchParams.get('consent'), 'true');
        uploaded++;
        hosted = { id: 'e'.repeat(32), filename: 'image.png', size: 68, status: 'parsing', text: '', error: '', message_id: null };
        return route.fulfill({ json: hosted });
      }
      if (parsed.pathname.endsWith('/poll')) {
        if (finish) hosted = { ...hosted, status: 'ready', text: '《云端模拟课程》 第三次作业截止2026年11月12日。' };
        return route.fulfill({ json: hosted });
      }
      return route.continue();
    });
    const beforeHosted = (await api('tasks')).length;
    dialog = await open();
    await dialog.getByRole('button', { name: /图片上传/ }).click();
    await dialog.getByLabel('选择通知截图或照片').setInputFiles({ name: 'image.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=', 'base64') });
    assert(await dialog.getByRole('button', { name: '发送到 MinerU 并解析 →', exact: true }).isDisabled());
    assert.equal(uploaded, 0);
    await dialog.getByRole('checkbox', { name: /我同意将此文件发送到 MinerU/ }).check();
    await dialog.getByRole('button', { name: '发送到 MinerU 并解析 →', exact: true }).click();
    await dialog.getByText('⏳ MinerU 正在解析…', { exact: true }).waitFor();
    await dialog.getByRole('button', { name: '关闭新建任务', exact: true }).click();
    await dialog.waitFor({ state: 'detached' });
    finish = true;
    dialog = await open();
    await dialog.getByRole('button', { name: /图片上传/ }).click();
    await dialog.getByText('最近上传 · 继续处理', { exact: true }).click();
    await dialog.locator('.ci-recentfile').filter({ hasText: 'image.png' }).click();
    await dialog.getByText('✅ 文本已提取，请校正后继续', { exact: true }).waitFor();
    assert.equal(await dialog.getByLabel('提取文本（可校正）', { exact: true }).inputValue(), hosted.text);
    assert.equal(uploaded, 1);
    assert.equal((await api('tasks')).length, beforeHosted);
    await dialog.getByRole('button', { name: '关闭新建任务', exact: true }).click();
    await page.unroute('**/api/imports/**');
    assert.deepEqual(errors, []);
    console.log('Unified creation: text review, local upload/resume, original downloads, manual date-only task, four modes, LAN crypto compatibility, cloud consent/recovery (mocked), missing cloud config and 1440/390/320px verified.');
  } finally {
    if (browser) await browser.close();
    await fixture.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
