'use strict';
const assert=require('node:assert/strict'),{spawnSync}=require('node:child_process'),{chromium}=require('playwright');
const {createTestServer}=require('./lib/browser-test-server.cjs');
const fixture=createTestServer('notice-body-requirements-');let browser;
(async()=>{
 const seed=`import sys
from pathlib import Path
from unittest.mock import patch
from courseinbox.core import Inbox
from courseinbox.chaoxing import ingest_web_message,refine_notice_candidates
box=Inbox(Path(sys.argv[1])/'inbox.sqlite3')
def legacy(candidates,external):
 result=refine_notice_candidates(candidates,external)
 for c in result:c['requirements']=[]
 return result
for i,(title,body) in enumerate([('《哈哈哈》课程要结课了，作业通知','作业 10 月 31 日交'),('《哈哈哈》课程考试','明天考试，注意考试')]):
 with patch('courseinbox.chaoxing.refine_notice_candidates',side_effect=legacy):
  r=ingest_web_message(box,dict(kind='course_notice',message_id='original-'+str(i),title=title,text=body,course='哈哈哈',conversation='哈哈哈',captured_at='2026-10-29T10:36:00+08:00',url='https://notice.chaoxing.com/pc/notice/$CACG$body'+str(i)+'/detail',attachments=[]))
 box.confirm(r['proposals'][0]['id'],{'deadline':None})
`;
 const seeded=spawnSync(fixture.python,['-B','-X','utf8','-c',seed,fixture.directory],{cwd:fixture.root,encoding:'utf8',windowsHide:true});assert.equal(seeded.status,0,seeded.stderr);
 const {url,api}=await fixture.start();const before=await api('tasks');assert.equal(before.length,2);assert(before.every(t=>t.checklist.length===0));
 browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 for(const task of before){await page.goto(url+'/?task='+task.id+'#tasks');await page.getByRole('button',{name:'重新提取原通知',exact:true}).click();await page.locator('#taskDialog').waitFor({state:'hidden'});await page.locator('.proposal-card').first().waitFor()}
 const pending=await api('proposals');assert.equal(pending.length,2);assert(pending.every(p=>before.some(t=>t.id===p.target_id)));
 assert((await api('tasks')).every(t=>t.checklist.length===0));
 await page.goto('about:blank');await page.goto(url+'/#inbox');await page.locator('.proposal-card').first().waitFor();
 const values=await page.locator('[data-field="requirements"]').evaluateAll(nodes=>nodes.map(n=>n.value));assert(values.some(s=>s.includes('作业 10 月 31 日交')));assert(values.some(s=>s.includes('明天考试，注意考试')));
 assert.equal(await page.evaluate(()=>formatDeadline('2026-10-31')),'2026.10.31（仅日期）');
 assert.equal(await page.evaluate(()=>formatDeadline(null)),'截止待明确');
 for(const p of pending){await page.locator(`[data-proposal="${p.id}"] [data-confirm]`).click();await page.locator(`[data-proposal="${p.id}"]`).waitFor({state:'detached'})}
 const tasks=await api('tasks');assert.equal(tasks.length,2);assert(tasks.every(t=>before.some(old=>old.id===t.id)));
 const work=tasks.find(t=>t.kind==='assignment'),exam=tasks.find(t=>t.kind==='exam');assert.equal(work.deadline,'2026-10-31');assert.equal(exam.deadline,null);
 for(const [task,text] of [[work,'作业 10 月 31 日交'],[exam,'明天考试，注意考试']]){
  await page.goto(url+'/?task='+task.id+'#tasks');await page.locator('.task-requirements').waitFor();assert.match(await page.locator('.task-requirements').inputValue(),new RegExp(text));
  for(const width of [1280,390]){await page.setViewportSize({width,height:1000});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1))}
 }
 assert.deepEqual(errors,[]);console.log('PASS old notices re-extract into same tasks, raw requirements displayed, hand-in date recognized, exam schedule not deadline, no duplicate tasks or premature writes');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{try{await browser?.close()}finally{await fixture.close()}});
