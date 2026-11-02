'use strict';
window.CourseInboxQQ={mount(parent,{json,esc,toast}){
 const panel=document.createElement('section');panel.className='panel';panel.id='qqConnector';
 panel.innerHTML=`<h2>QQ 消息接入</h2><p class="helper">需配置 OneBot v11 消息桥，只接收下方允许的课程群。</p><p id="qqState" role="status">正在读取…</p><p id="qqCounts" class="helper"></p><form id="qqForm"><label class="field">允许的QQ群（每行：群号 | 群名 | 课程，可省略课程）<textarea id="qqGroups" rows="3" maxlength="12000" placeholder="123456789 | 软件工程课程群 | 软件工程"></textarea></label><label><input id="qqEnabled" type="checkbox"> 开启接收</label><div class="toolbar"><button class="button primary" id="qqSave" disabled>保存设置</button><button class="button" id="qqRetry" type="button" disabled>重试失败消息</button><button class="text-button" id="qqRefresh" type="button">刷新状态</button></div></form><details><summary>消息桥配置</summary><label class="field">HTTP 事件上报地址<input id="qqEndpoint" readonly></label><label class="field">签名 secret（仅用于 QQ 接入）<input id="qqSecret" type="password" readonly autocomplete="off"></label><button class="button small" id="qqCopySecret">复制接入密钥</button><p class="helper">把地址和 secret 填入消息桥的 HTTP POST 上报配置。图片、文件暂保留来源，不自动下载。</p></details><p id="qqError" class="error-text" role="alert"></p>`;
 parent.append(panel);let loaded=false,revision=0,timer,keyTimer;
 const $=s=>panel.querySelector(s);
 function paint(value){
  $('#qqState').textContent=value.error||(value.enabled?(value.listening?'接收入口已开启':'接收入口启动失败'):'未开启');
  $('#qqCounts').textContent=`已处理 ${value.counts.done} · 待处理 ${value.counts.pending+value.counts.processing} · 失败 ${value.counts.failed}`+(value.last_received_at?' · 最近消息 '+new Date(value.last_received_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'');
  $('#qqEndpoint').value=value.endpoint;$('#qqRetry').disabled=!value.counts.failed;
  if(!loaded){$('#qqGroups').value=value.groups.map(g=>[g.id,g.name,g.course].join(' | ')).join('\n');$('#qqEnabled').checked=value.enabled;loaded=true}
  $('#qqSave').disabled=false;
 }
 async function refresh(){try{const value=await json('/api/qq-connector/status');if(panel.isConnected)paint(value)}catch{$('#qqError').textContent='QQ 接入状态暂不可用；若刚更新项目，请重启后刷新。'}}
 $('#qqForm').addEventListener('input',()=>revision++);
 $('#qqForm').addEventListener('submit',async event=>{
  event.preventDefault();const current=revision;$('#qqSave').disabled=true;$('#qqError').textContent='';
  try{
   const groups=$('#qqGroups').value.split('\n').filter(s=>s.trim()).map(line=>{const parts=line.split('|').map(s=>s.trim());if(parts.length<2||parts.length>3)throw Error('每行请填写：群号 | 群名 | 课程（课程可省略）。');return {id:parts[0],name:parts[1],course:parts[2]||''}});
   const result=await json('/api/qq-connector/settings',{method:'POST',body:{enabled:$('#qqEnabled').checked,groups}});paint(result);toast(current===revision?'QQ 接入设置已保存':'上一份设置已保存，当前修改尚未保存');
  }catch(error){$('#qqError').textContent=error.message}finally{$('#qqSave').disabled=false}
 });
 $('#qqRetry').addEventListener('click',async()=>{try{paint(await json('/api/qq-connector/retry',{method:'POST',body:{}}));toast('已安排重试')}catch(error){$('#qqError').textContent=error.message}});
 $('#qqRefresh').addEventListener('click',refresh);
 $('#qqCopySecret').addEventListener('click',async()=>{
  try{const result=await json('/api/qq-connector/credential');$('#qqSecret').value=result.secret;clearTimeout(keyTimer);keyTimer=setTimeout(()=>{$('#qqSecret').value=''},60000);try{await navigator.clipboard.writeText(result.secret);toast('QQ 接入密钥已复制')}catch{toast('密钥已载入，请从输入框复制')}}catch(error){$('#qqError').textContent=error.message}
 });
 refresh();timer=setInterval(()=>{if(!panel.isConnected){clearInterval(timer);clearTimeout(keyTimer);$('#qqSecret').value='';return}refresh()},5000);
}};
