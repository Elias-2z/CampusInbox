'use strict';
const assert=require('node:assert/strict'),http=require('node:http'),crypto=require('node:crypto'),{chromium}=require('playwright');
const {createTestServer}=require('./lib/browser-test-server.cjs');
const fixture=createTestServer('chaoxing-model-fallback-');let browser,requests=0,fail=false;
const llm=http.createServer((req,res)=>{let raw='';req.on('data',c=>raw+=c);req.on('end',()=>{requests++;if(fail){res.writeHead(503);res.end('{}');return}const data=JSON.parse(raw);const source=JSON.parse(data.messages[1].content).source_message;let message={role:'assistant',content:'没有额外任务。'};
 if(source.text.includes('实验一')&&data.messages.at(-1).role==='user')message={role:'assistant',content:'',tool_calls:[{id:'proposal-1',type:'function',function:{name:'propose_task',arguments:JSON.stringify({title:'实验一',course:'软件工程',kind:'experiment',deadline:'2026-11-02T20:00:00+08:00',deadline_evidence:'最晚11月2日20:00',submission_method:'邮件提交',submission_evidence:'发送至课程邮箱',requirements:[],evidence:source.text})}}]};
 res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({choices:[{message}]}));});});
(async()=>{
 await new Promise(resolve=>llm.listen(0,'127.0.0.1',resolve));
 process.env.CI_MODEL_PROVIDER='openai';process.env.CI_MODEL_BASE_URL='http://127.0.0.1:'+llm.address().port;process.env.CI_MODEL='local-test-model';process.env.CI_MODEL_API_KEY='dummy-test-key';
 const {url,api,token}=await fixture.start();const webToken=crypto.createHmac('sha256',token).update('courseinbox:chaoxing-web:v1').digest('hex');
 browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 async function send(id,text,title='普通通知'){const r=await page.request.post(url+'/api/chaoxing/messages',{headers:{Authorization:'Bearer '+webToken},data:{kind:'course_notice',message_id:id,title,text,course:'软件工程',conversation:'课程群',captured_at:'2026-10-29T18:04:00+08:00',url:'https://notice.chaoxing.com/pc/notice/$CACG$'+id+'/detail',attachments:[]}});assert(r.ok(),await r.text());}
 await send('garbage','这是一条垃圾信息哈哈哈');assert.equal(requests,1);await send('garbage','这是一条垃圾信息哈哈哈');assert.equal(requests,1);
 await send('complete','实验四：截止2026年11月4日20:00，提交方式：学习通上传。','《软件工程》实验四');assert.equal(requests,1,'complete rule fields must skip paid review');
 await send('task','实验一：最晚11月2日20:00发送至课程邮箱。','《软件工程》实验一');assert.equal(requests,2);
 const proposals=await api('proposals');assert.equal(proposals.length,2);const reviewed=proposals.find(p=>p.candidate.title==='实验一');assert.equal(reviewed.candidate.submission_method,'邮件提交');assert.equal(reviewed.candidate.deadline,'2026-11-02T20:00:00+08:00');assert.equal((await api('tasks')).length,0);
 fail=true;await send('failed','实验二：请将报告投递到课程邮箱。','《软件工程》实验二');
 await page.goto(url+'/#inbox');await page.locator('.proposal-card .recognition-status').first().waitFor();
 assert.equal(await page.locator('.proposal-card .recognition-status').filter({hasText:'模型已调用'}).count(),1);
 await page.locator('[data-inbox-tab="messages"]').click();await page.locator('.recognition-status').first().waitFor();
 assert.equal(await page.locator('.recognition-status').filter({hasText:'模型已调用'}).count(),2);
 assert.equal(await page.locator('.recognition-status').filter({hasText:'模型失败，回退规则'}).count(),1);
 const messages=await api('messages/page?limit=20');const task=messages.items.find(m=>m.external?.message_id==='task');assert(task);
 await page.locator(`[data-message-detail="${task.id}"]`).click();await page.locator('#messageDetail .recognition-status').waitFor();assert.match(await page.locator('#messageDetail .recognition-status').innerText(),/local-test-model.*1 次请求/);
 for(const width of [1280,390]){await page.setViewportSize({width,height:1000});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));}
 assert.deepEqual(errors,[]);console.log('PASS real HTTP intake/model adapter, no-task review, field fill, duplicate request suppression, failure fallback, visible status/details, no premature task writes');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{try{await browser?.close()}finally{await fixture.close();await new Promise(resolve=>llm.close(resolve))}});
