'use strict';
const assert=require('node:assert/strict'),{chromium}=require('playwright');
const {createTestServer}=require('./lib/browser-test-server.cjs');
const fixture=createTestServer('draft-deadline-edit-');let browser;
(async()=>{
 const {url,api}=await fixture.start();
 await api('connectors/chat/import',{platform:'qq',text:'实验一：使用Python编写程序。',course:'软件工程',conversation:'测试群',published_at:'2026-10-29T18:04:00+08:00',mode:'rules'});
 browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage();
 await page.goto(url+'/#inbox');const card=page.locator('.proposal-card').first();await card.waitFor();
 const field=card.locator('[data-field="deadline"]'),precision=card.locator('[data-precision]');
 assert.equal(await precision.inputValue(),'unknown');assert(await field.isEnabled(),'unknown deadline must remain editable');
 await field.fill('2026-10-31');assert.equal(await precision.inputValue(),'date');
 await precision.selectOption('unknown');assert.equal(await field.inputValue(),'');assert(await field.isEnabled());
 await field.fill('2026-11-02');await card.locator('[data-confirm]').click();
 await card.waitFor({state:'detached'});const tasks=await api('tasks');assert.equal(tasks.length,1);assert.equal(tasks[0].deadline,'2026-11-02');
 console.log('PASS unknown deadline directly editable, precision follows input, unknown clears date, confirmed task saves manual deadline');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{try{await browser?.close()}finally{await fixture.close()}});
