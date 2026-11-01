'use strict';
const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require('playwright');
const {createTestServer}=require('./lib/browser-test-server.cjs');
(async()=>{
 const fixture=createTestServer('campus-integrated-');
 let browser;
 try{
  const {url,api}=await fixture.start();
  browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);await page.getByRole('heading',{name:'嗨，同学！开个好头 ✌️'}).waitFor();
  await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:/我的任务/}).click();
  await page.getByRole('button',{name:'＋ 新建任务',exact:true}).click();
  const createDialog=page.getByRole('dialog',{name:'✨ 新建任务',exact:true});
  await createDialog.getByLabel('通知原文',{exact:true}).fill('《融合验收课程》 第一次作业：请提交实验报告、源代码。截止2026年11月5日18:00。');
  await createDialog.getByLabel('消息实际发送时间').fill('2026-11-01T08:00');
  await createDialog.getByRole('button',{name:'识别并前往收件箱审核 →',exact:true}).click();
  await createDialog.waitFor({state:'detached'});
  let frame;
  const imported=(await api('state')).proposals;assert.equal(imported.length,1);
  await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:/收件箱/}).click();
  frame=page.frameLocator('.ci-workspace-frame');await frame.locator('.proposal-card').waitFor();
  await frame.locator('[data-confirm]').first().click();
  await frame.locator('.proposal-card').waitFor({state:'detached'});
  const task=(await api('tasks'))[0];assert.equal(task.course,'融合验收课程');assert.equal(task.checklist.length,2);
  await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:/今天/}).click();
  await page.locator('.ci-message-preview').first().click();
  frame=page.frameLocator('.ci-panel-frame');await frame.locator('#messageDialog[open]').waitFor();
  await frame.locator('#messageDetail [data-detail]').first().click();
  await frame.locator('#taskDialog[open]').waitFor();
  await frame.locator('#taskDialog .close-button').click();
  const draft=await api('messages',{text:'《跳转审核课程》 第二次作业截止2026年11月6日18:00。',captured_at:'2026-11-01T08:00:00+08:00'});
  await page.goto(url+'?message='+draft.message.id);
  frame=page.frameLocator('.ci-panel-frame');await frame.locator('#messageDialog[open]').waitFor();
  await frame.locator('[data-message-proposal]').first().click();
  await page.locator('.ci-panel-frame').waitFor({state:'detached'});
  await page.frameLocator('.ci-workspace-frame').locator('.proposal-card').waitFor();
  await api('proposals/'+draft.proposals[0].id+'/dismiss',{});

  await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:/我的任务/}).click();
  await page.getByRole('button',{name:task.title,exact:true}).click();
  frame=page.frameLocator('.ci-panel-frame');await frame.locator('#taskDialog[open]').waitFor();
  const materialSaved=page.waitForResponse(r=>r.url().endsWith('/api/tasks/'+task.id)&&r.request().method()==='PATCH');
  await frame.locator('#taskDetail input[type=checkbox]').first().check();await materialSaved;
  assert.equal((await api('tasks/'+task.id)).checklist[0].done,true);
  await frame.locator('#taskDialog[open]').waitFor();
  await frame.locator('#taskDialog .close-button').click();
  await page.locator('.ci-panel-frame').waitFor({state:'detached'});
  await page.getByText('材料 1/2',{exact:true}).waitFor();
  await page.getByRole('button',{name:'标记做完 '+task.title,exact:true}).click();
  await page.getByText('已记为做完，仍需在原平台提交。',{exact:true}).waitFor();
  assert.equal((await api('tasks/'+task.id)).status,'done');
  await page.getByRole('button',{name:'确认已提交 '+task.title,exact:true}).click();
  assert.equal((await api('tasks/'+task.id)).status,'done');
  await page.getByRole('dialog',{name:'确认记为已提交？'}).getByRole('button',{name:'确认',exact:true}).click();
  await page.getByRole('button',{name:'撤销',exact:true}).waitFor();
  await page.waitForFunction(async()=>{const b=await(await fetch('/api/bootstrap')).json();const t=await(await fetch('/api/tasks',{headers:{Authorization:'Bearer '+b.token}})).json();return t[0].status==='submitted'});
  await page.getByRole('button',{name:'撤销',exact:true}).click();
  await page.getByRole('button',{name:'确认已提交 '+task.title,exact:true}).waitFor();
  await api('messages',{text:'《仅日期课程》 第一次作业截止2026年11月8日。',captured_at:'2026-11-01T08:00:00+08:00'});
  const p=(await api('state')).proposals[0];await api('proposals/'+p.id+'/confirm',{});
  await page.getByRole('button',{name:'刷新数据'}).click();await page.getByText('2026/11/08（仅日期）',{exact:true}).waitFor();
  const nav=page.getByRole('navigation',{name:'主导航'});
  const shifted=await api('messages',{text:'《融合验收课程》 第一次作业延期到2026年11月7日18:00，其他要求不变。',captured_at:'2026-11-01T09:00:00+08:00'});
  assert.equal(shifted.proposals.length,1);
  await nav.getByRole('button',{name:/收件箱/}).click();
  frame=page.frameLocator('.ci-workspace-frame');
  await frame.locator('[data-proposal="'+shifted.proposals[0].id+'"] [data-confirm]').click();
  await frame.locator('[data-proposal="'+shifted.proposals[0].id+'"]').waitFor({state:'detached'});
  const updated=await api('tasks/'+task.id);assert.match(updated.deadline,/2026-11-07/);assert.equal(updated.checklist[0].done,true);assert.equal(updated.status,'done');assert.equal((await api('tasks')).length,2);

  await nav.getByRole('button',{name:/日历/}).click();await page.locator('.ci-calendar-grid').waitFor();
  const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'导出全部日历 .ics'}).click()]);assert.equal(download.suggestedFilename(),'campusinbox.ics');
  await nav.getByRole('button',{name:/提醒/}).click();await page.getByRole('button',{name:'开启浏览器通知'}).waitFor();
  for(const [name,selector] of [['学习通同步','#content[data-page=sync]'],['手机连接','#content[data-page=mobile]']]){
   await nav.getByRole('button',{name:/设置/}).click();
   await page.getByRole('button',{name,exact:true}).click();
   await page.frameLocator('.ci-workspace-frame').locator(selector).waitFor();
  }
  await nav.getByRole('button',{name:/设置/}).click();await page.getByRole('heading',{name:'🗃️ 数据与备份'}).waitFor();
  await nav.getByRole('button',{name:/今天/}).click();await page.screenshot({path:path.join(__dirname,'../docs/verification/integrated-ui-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  for(const name of ['今天','我的任务','日历','收件箱','设置']){
   await nav.getByRole('button',{name:new RegExp(name)}).click();
   await page.waitForTimeout(200);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),name+' overflows horizontally');
  }
  await nav.getByRole('button',{name:/日历/}).click();await page.screenshot({path:path.join(__dirname,'../docs/verification/integrated-ui-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('Integrated UI: import, review, materials, done/submitted, undo, date precision, calendar export, all workspaces, settings, mobile and browser errors verified.');
 }finally{if(browser)await browser.close();await fixture.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
