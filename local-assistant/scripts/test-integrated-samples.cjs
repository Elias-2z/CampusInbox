'use strict';
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const {createTestServer}=require('./lib/browser-test-server.cjs');
(async()=>{const fixture=createTestServer('campus-sample-ui-');let browser;try{const {url,api}=await fixture.start();await api('demo',{});browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage();await page.goto(url);await page.locator('.ci-task').first().waitFor();assert.equal(await page.locator('.ci-task').first().locator('.ci-sample').count(),1);assert.equal(await page.locator('.ci-message-preview').first().locator('.ci-sample').count(),1);console.log('Synthetic tasks and messages retain visible sample labels')}finally{if(browser)await browser.close();await fixture.close()}})().catch(e=>{console.error(e);process.exitCode=1});
