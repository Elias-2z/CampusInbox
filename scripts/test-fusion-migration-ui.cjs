'use strict';
// Optional browser regression; all records and services belong to this test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { chromium } = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const temporaryRoot = fs.realpathSync(os.tmpdir());
  const directory = fs.mkdtempSync(path.join(temporaryRoot, 'campus-fusion-migration-'));
  let backend, stopped, frontend, browser;
  try {
    const python = path.join(root, 'backend/.venv/Scripts/python.exe');
    backend = spawn(python, ['-u', '-c', [
      'import socket,sys,uvicorn',
      'from pathlib import Path',
      'from campus_inbox.main import create_app',
      'app=create_app(Path(sys.argv[1]),qq_background=False,chaoxing_background=False)',
      'sock=socket.socket();sock.bind(("127.0.0.1",0))',
      'print("ready:"+str(sock.getsockname()[1]),flush=True)',
      'uvicorn.Server(uvicorn.Config(app,log_level="error")).run(sockets=[sock])',
    ].join('\n'), path.join(directory, 'main.db')], { cwd: root, windowsHide: true });
    stopped = new Promise(resolve => backend.once('close', resolve));
    const port = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('isolated backend startup timeout')), 15000);
      let text = '';
      backend.stdout.on('data', chunk => {
        text += chunk.toString();
        const match = text.match(/ready:(\d+)/);
        if (match) { clearTimeout(timer); resolve(Number(match[1])); }
      });
      backend.stderr.on('data', () => {});
      backend.once('error', error => { clearTimeout(timer); reject(error); });
    });
    const api = 'http://127.0.0.1:' + port;
    for (let i = 0; i < 50; i++) {
      try { if ((await fetch(api + '/api/health')).ok) break; } catch {}
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    const { preview } = await import(pathToFileURL(path.join(root, 'frontend/node_modules/vite/dist/node/index.js')).href);
    frontend = await preview({
      root: path.join(root, 'frontend'), configFile: false,
      preview: { host: '127.0.0.1', port: 0, proxy: { '/api': api } },
    });
    const url = 'http://127.0.0.1:' + frontend.httpServer.address().port;
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const data = {
      version: '0.1.0',
      messages: [
        { id: 'm1', text: '第一次作业请提交报告', captured_at: '2026-11-01T12:00:00+08:00' },
        { id: 'm2', text: '截止改为12月8日', external: { time_known: false } },
      ],
      proposals: [],
      tasks: [{
        id: 't1', title: '迁移检查报告', course: '软件工程', kind: 'assignment', status: 'done',
        deadline: '2026-10-08', sources: [{ id: 'm1' }, { id: 'm2' }],
        checklist: [{ text: '报告', done: true }],
        history: [{ action: 'create', at: '2026-11-01T12:00:00+08:00', after: { title: '迁移检查报告' } }],
      }],
    };
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 950 });
      await page.goto(url + '/settings');
      const section = page.getByRole('region', { name: '旧版数据迁移检查' });
      await section.locator('input[type=file]').setInputFiles({
        name: 'synthetic-old.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(data)),
      });
      await section.getByRole('button', { name: '检查文件', exact: true }).click();
      const confirm = section.getByRole('button', { name: '备份并确认导入', exact: true });
      await confirm.waitFor();
      assert(await confirm.isDisabled(), 'explicit review must be required');
      await section.locator('input[type=checkbox]').check();
      await confirm.click();
      await section.getByRole('status').filter({ hasText: '迁移完成' }).waitFor();
      const tasks = await (await fetch(api + '/api/tasks')).json();
      assert.equal(tasks.length, 1);
      assert.equal(tasks[0].status, 'done');
      await page.goto(url + '/tasks/' + tasks[0].id);
      await page.getByRole('heading', { name: '迁移检查报告', exact: true }).waitFor();
      await page.waitForLoadState('networkidle');
      await page.getByText('📦 旧版原始历史 · 1 条', { exact: true }).click();
      await page.getByText('📎 通知来源 · 2 条', { exact: true }).waitFor();
      await page.getByText('发布时间未知', { exact: false }).waitFor();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), width + ': horizontal overflow');
      assert(!(await page.locator('body').innerText()).includes('1970'));
      console.log(width + 'px: migration confirmation, idempotent repeat, task, materials, multiple sources and historic details verified');
    }
    const inboxItem = await (await fetch(api + '/api/inbox', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: '请提交实验报告。', sent_at: '2026-11-03T09:00:00+08:00' }),
    })).json();
    assert(inboxItem.id);
    const attachment = { id: 'synthetic-ocr', filename: '要求.png', size: 100, status: 'parsing' };
    let analysisRequests = 0;
    const currentItem = () => ({
      ...inboxItem,
      status: attachment.status === 'parsing' ? 'processing' : 'pending',
      message: { ...inboxItem.message, attachments: [attachment] },
    });
    await page.route('**/api/inbox?status=all', route => route.fulfill({
      json: [currentItem()],
    }));
    await page.route('**/api/imports/capabilities', route => route.fulfill({
      json: { local_ocr: true, local_docx: true },
    }));
    let attempts = 0;
    await page.route('**/api/imports/files/synthetic-ocr/local-ocr', route => {
      if (++attempts === 1) return route.fulfill({ status: 503, json: { detail: '模拟本机 OCR 失败' } });
      attachment.status = 'ready';
      return route.fulfill({ json: { ...attachment, text: '请提交实验报告。', error: '' } });
    });
    await page.route('**/api/inbox/' + inboxItem.id + '/analyze', route => {
      analysisRequests++;
      inboxItem.fields.title = '附件识别结果';
      return route.fulfill({ json: currentItem() });
    });
    await page.goto(url + '/inbox?id=' + inboxItem.id);
    await page.getByText('下载原始附件：要求.png', { exact: false }).waitFor();
    assert.equal(await page.getByRole('button', { name: '本机解析附件' }).count(), 0);
    attachment.status = 'failed';
    await page.getByRole('button', { name: '刷新结果' }).click();
    await page.getByRole('button', { name: '本机解析附件' }).click();
    await page.getByRole('alert').filter({ hasText: '模拟本机 OCR 失败' }).waitFor();
    console.log('Attachment status refresh enables local retry and shows failures outside collapsed details');
    await page.getByRole('button', { name: '本机解析附件' }).click();
    const useAttachment = page.getByRole('button', { name: '结合附件重新识别' });
    await useAttachment.waitFor();
    const title = page.getByLabel(/任务名称/);
    await title.fill('暂存的人工修改');
    page.once('dialog', dialog => dialog.dismiss());
    await useAttachment.click();
    assert.equal(analysisRequests, 0);
    assert.equal(await title.inputValue(), '暂存的人工修改');
    page.once('dialog', dialog => dialog.accept());
    await useAttachment.click();
    await page.getByRole('status').filter({ hasText: '重新识别完成' }).waitFor();
    assert.equal(analysisRequests, 1);
    await page.waitForFunction(() => document.querySelector('input[placeholder="这件事叫什么？"]')?.value === '附件识别结果');
    assert.equal(await title.inputValue(), '附件识别结果');
    console.log('Attachment reanalysis preserves manual edits until replacement is confirmed');
    const [task] = await (await fetch(api + '/api/tasks')).json();
    const taskDetail = await (await fetch(api + '/api/tasks/' + task.id)).json();
    taskDetail.source.source_context = { platform: 'chaoxing', kind: 'checkin_activity' };
    taskDetail.checkin_closed = false;
    await page.route('**/api/tasks/' + task.id, route => route.fulfill({ json: taskDetail }));
    await page.goto(url + '/tasks/' + task.id);
    const checkin = page.getByRole('region', { name: '学习通签到' });
    await checkin.getByText('该场签到按学习通活动截止时间提醒。').waitFor();
    taskDetail.checkin_closed = true;
    await checkin.getByRole('button', { name: '刷新本地记录' }).click();
    await checkin.getByText('该场签到已结束，提醒已停止。', { exact: false }).waitFor();
    assert.equal(await page.getByRole('region', { name: '学习通状态', exact: true }).count(), 0);
    console.log('Task details distinguish active and ended check-in reminders');
    await page.getByRole('button', { name: '课务助手' }).click();
    const assistant = page.getByRole('dialog');
    await assistant.getByRole('textbox').fill('已做完任务');
    await assistant.getByRole('button', { name: '查询任务' }).click();
    await assistant.getByRole('button', { name: '查看材料' }).click();
    await assistant.getByText('已准备 · 报告').waitFor();
    await assistant.getByRole('textbox').fill('尚未提交任务');
    await assistant.getByRole('button', { name: '查询任务' }).click();
    await assistant.getByText('找到 0 项任务').waitFor();
    console.log('Rule assistant reads saved materials and excludes overdue work from unsubmitted results');
    await page.goto(url + '/settings/connections/wechat');
    const push = page.getByRole('region', { name: '微信截止提醒' });
    const enable = push.getByRole('checkbox', { name: '启用微信截止提醒' });
    await enable.waitFor();
    assert(!(await enable.isChecked()));
    assert(await push.getByRole('checkbox', { name: '同时提醒新发布的学习通课程通知' }).isDisabled());
    assert(await push.getByRole('checkbox', { name: /新聊天、新作业考试/ }).isDisabled());
    assert(await push.getByRole('checkbox', { name: '同时提醒学习通任务审核结果' }).isDisabled());
    await push.getByLabel('PushPlus Token').fill('synthetic_pushplus_token');
    await push.getByRole('button', { name: '保存推送设置' }).click();
    const testPush = push.getByRole('button', { name: '发送测试提醒' });
    await page.route('**/api/connectors/wechat/push/test', route => route.fulfill({
      json: { supported: true, configured: true, enabled: false, result: 'accepted' },
    }));
    await testPush.click();
    await push.getByText('服务已受理，实际送达请在微信核对').waitFor();
    assert.equal(await push.getByLabel('PushPlus Token').inputValue(), '');
    await push.getByRole('button', { name: '关闭并清除 Token' }).click();
    await push.getByText('暂无发送记录').waitFor();
    assert(await testPush.isDisabled());
    console.log('WeChat push stays off by default and supports encrypted configuration, simulated test, and credential clearing');

    await page.unroute('**/api/inbox?status=all');
    await page.unroute('**/api/tasks/' + task.id);
    const postponement = await (await fetch(api + '/api/inbox', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: '软件工程：迁移检查报告延期到2099年12月5日。',
        sent_at: '2026-11-03T09:00:00+08:00',
      }),
    })).json();
    await page.goto(url + '/inbox?id=' + postponement.id);
    const date = page.getByLabel('新的截止日期（没有具体时间时填写）');
    await date.waitFor();
    assert.equal(await date.inputValue(), '2099-12-05');
    assert.equal(await page.locator('input[type=datetime-local]').inputValue(), '');
    await page.getByLabel('要更新哪项任务？').selectOption(task.id);
    await page.getByRole('button', { name: /预览这次变更/ }).click();
    const deadlinePreview = page.getByRole('region', { name: '截止时间变更预览' });
    await deadlinePreview.getByText(/仅日期/).last().waitFor();
    const before = await (await fetch(api + '/api/tasks/' + task.id)).json();
    assert.equal(before.task.due_date, task.due_date);
    await deadlinePreview.getByRole('button', { name: /确认仅更新截止时间/ }).click();
    await page.waitForURL('**/tasks/' + task.id);
    const after = await (await fetch(api + '/api/tasks/' + task.id)).json();
    assert.equal(after.task.due_date, '2099-12-05');
    assert.equal(after.task.due_at, null);
    console.log('Date-only postponement is prefilled, previewed and applied only after confirmation');
    assert.deepEqual(errors, []);
  } finally {
    if (browser) await browser.close();
    if (frontend) await new Promise(resolve => frontend.httpServer.close(resolve));
    if (backend && backend.exitCode === null) backend.kill();
    if (stopped) await stopped;
    assert.equal(path.dirname(directory), temporaryRoot);
    assert(path.basename(directory).startsWith('campus-fusion-migration-'));
    fs.rmSync(directory, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
