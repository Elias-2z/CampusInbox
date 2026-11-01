'use strict';
const assert=require('node:assert/strict'),crypto=require('node:crypto'),{chromium}=require('playwright');
const {createTestServer}=require('./lib/browser-test-server.cjs');
const fixture=createTestServer('qq-connector-ui-');let browser;
(async()=>{
 const {url,api,token}=await fixture.start();browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url+'/#sync');await page.locator('#qqSave:not([disabled])').waitFor();
 assert.equal(await page.locator('#qqSecret').inputValue(),'');
 await page.locator('#qqGroups').fill('12345 | 软件工程课程群 | 软件工程');await page.locator('#qqEnabled').check();await page.locator('#qqSave').click();
 await page.locator('#qqState').filter({hasText:'接收入口已开启'}).waitFor();
 const status=await api('qq-connector/status'),{secret}=await api('qq-connector/credential');assert(!JSON.stringify(status).includes(secret));
 const response=await page.request.get(url+'/api/tasks',{headers:{Authorization:'Bearer '+secret}});assert.equal(response.status(),401);
 const event={post_type:'message',message_type:'group',self_id:11111,group_id:12345,user_id:22222,message_id:101,time:Math.floor(new Date('2026-10-29T14:00:00+08:00').getTime()/1000),message:[{type:'text',data:{text:'第三次作业：明天20:00前提交PDF报告。'}}],sender:{nickname:'老师'}};
 async function send(value){const raw=JSON.stringify(value),signature='sha1='+crypto.createHmac('sha1',secret).update(raw).digest('hex');const result=await page.request.post(status.endpoint,{data:raw,headers:{'Content-Type':'application/json','X-Signature':signature}});assert.equal(result.status(),200,await result.text());return result.json()}
 await send(event);
 await page.waitForFunction(async access=>{const r=await fetch('/api/qq-connector/status',{headers:{Authorization:'Bearer '+access}});return (await r.json()).counts.done===1},token);
 const repeated=await send(event);assert(repeated.duplicate);
 let tasks=await api('tasks');assert.equal(tasks.length,0);
 let state=await api('state');assert.equal(state.proposals.length,1);assert.equal(state.messages[0].chat_import.transport,'onebot11');assert.equal(state.proposals[0].candidate.deadline,'2026-10-30T20:00:00+08:00');
 await page.goto('about:blank');await page.goto(url+'/#inbox');await page.locator('.proposal-card').waitFor();assert.match(await page.locator('.source-line').innerText(),/QQ（Connector）/);
 await page.locator('[data-confirm]').click();await page.locator('.proposal-card').waitFor({state:'detached'});tasks=await api('tasks');assert.equal(tasks.length,1);
 event.message_id=102;event.message=[{type:'text',data:{text:'第三次作业截止时间延期到2026年11月2日20:00。'}}];await send(event);
 await page.waitForFunction(async access=>{const r=await fetch('/api/qq-connector/status',{headers:{Authorization:'Bearer '+access}});return (await r.json()).counts.done===2},token);
 state=await api('state');assert.equal(state.proposals.length,1);assert.equal(state.proposals[0].target_id,tasks[0].id);assert.equal((await api('tasks'))[0].deadline,'2026-10-30T20:00:00+08:00');
 await page.reload();await page.getByRole('button',{name:'确认变更',exact:true}).click();await page.locator('.proposal-card').waitFor({state:'detached'});tasks=await api('tasks');assert.equal(tasks.length,1);assert.equal(tasks[0].deadline,'2026-11-02T20:00:00+08:00');
 await page.goto('about:blank');await page.goto(url+'/#sync');await page.locator('#qqSave:not([disabled])').waitFor();
 await page.locator('#qqGroups').fill('12345 | 尚未保存的修改 | 软件工程');await page.waitForTimeout(5200);assert.match(await page.locator('#qqGroups').inputValue(),/尚未保存/);
 for(const width of [1440,390,320]){await page.setViewportSize({width,height:1000});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1))}
 await api('qq-connector/settings',{enabled:false,groups:[]});assert.equal((await api('qq-connector/status')).listening,false);
 assert.deepEqual(errors,[]);console.log('PASS QQ connector: configure, signed background intake, replay, draft confirmation, original-task deadline update, scoped auth, preserved edits and narrow layouts');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{try{await browser?.close()}finally{await fixture.close()}});
