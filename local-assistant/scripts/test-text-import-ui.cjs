'use strict';
const assert=require('node:assert/strict'),http=require('node:http'),{chromium}=require('playwright');
const {createTestServer}=require('./lib/browser-test-server.cjs');
const fixture=createTestServer('text-import-ui-');let browser,requests=0;
const llm=http.createServer((req,res)=>{let raw='';req.on('data',c=>raw+=c);req.on('end',()=>{requests++;const data=JSON.parse(raw),source=JSON.parse(data.messages[1].content).source_message;let message={role:'assistant',content:'原文已核对。'};
 if(source.text.includes('请复习')&&data.messages.at(-1).role==='user')message={role:'assistant',content:'',tool_calls:[{id:'review',type:'function',function:{name:'propose_task',arguments:JSON.stringify({title:'复习本章内容',course:'软件工程',kind:'other',deadline:null,deadline_evidence:'',requirements:['复习本章内容'],submission_method:'',submission_evidence:'',evidence:source.text})}}]};
 res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({choices:[{message}]}));});});
(async()=>{
 await new Promise(resolve=>llm.listen(0,'127.0.0.1',resolve));process.env.CI_MODEL_PROVIDER='openai';process.env.CI_MODEL_BASE_URL='http://127.0.0.1:'+llm.address().port;process.env.CI_MODEL='text-test-model';
 const {url,api}=await fixture.start();browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 assert.equal((await page.request.post(url+'/api/import/text',{data:{text:'测试'}})).status(),401);
 await page.goto(url+'/#tasks');await page.locator('#importOpen').click();
 assert(await page.locator('#noticeText').isVisible());assert.equal(await page.locator('#noticeTime').inputValue(),'');assert.equal(await page.locator('#importOptions').getAttribute('open'),null);
 assert.deepEqual(await page.locator('#importForm [required]').evaluateAll(ns=>ns.map(n=>n.id)),['noticeText']);
 await page.locator('#noticeText').fill('《软件工程》实验一：截止2026年11月3日20:30，提交方式：学习通上传。需使用Python编写程序，附上运行截图。');
 await page.locator('#importSubmit').click();await page.locator('#importDialog').waitFor({state:'hidden'});let p=(await api('proposals'))[0];
 assert.equal(p.candidate.course,'软件工程');assert.equal(p.candidate.kind,'experiment');assert.equal(p.candidate.deadline,'2026-11-03T20:30:00+08:00');assert.equal(p.candidate.submission_method,'学习通上传');assert(p.candidate.requirements.some(r=>r.includes('Python')));assert(p.candidate.requirements.some(r=>r.includes('截图')));assert.equal((await api('tasks')).length,0);assert.equal(requests,0);
 await page.locator('#importOpen').click();await page.locator('#noticeText').fill('课程：软件工程\n请复习本章内容。');await page.locator('#importSubmit').click();await page.locator('#importDialog').waitFor({state:'hidden'});
 p=(await api('proposals')).find(p=>p.candidate.title==='复习本章内容');assert(p);assert.equal(p.candidate.course,'软件工程');assert.equal(p.candidate.deadline,null);assert.equal(requests,1);
 await page.locator('#importOpen').click();for(const width of [1280,390]){await page.setViewportSize({width,height:1000});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1))}
 assert.deepEqual(errors,[]);console.log('PASS text-only form, optional context/files, authenticated API, course/type/date/method/requirements fill, model fallback without publication, no premature task writes, narrow layouts');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{try{await browser?.close()}finally{await fixture.close();await new Promise(resolve=>llm.close(resolve))}});
