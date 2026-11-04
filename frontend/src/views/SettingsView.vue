<script setup lang="ts">
import ReminderSettings from '@/components/ReminderSettings.vue'
import ModelSettings from '@/components/ModelSettings.vue'
</script>
<template>
  <div class="ci-pagehead">
    <div>
      <div class="ci-eyebrow">YOUR CAMPUS, YOUR PREFERENCES</div>
      <h1>按你的习惯来 ⚙️</h1>
    </div>
  </div>
  <RouterLink to="/settings/connections" class="ci-connection-entry">
    <span class="ci-connection-icon" aria-hidden="true">🔌</span>
    <span class="ci-connection-entry-copy"
      ><strong>数据源连接</strong><small>管理 QQ、学习通、微信的账号与通知接收范围</small></span
    >
    <span class="ci-connection-entry-arrow" aria-hidden="true">→</span>
  </RouterLink>
  <ReminderSettings />
  <div class="ci-settingslayout">
    <ModelSettings />
    <aside class="ci-source">
      <h3>🔒 信息如何处理</h3>
      <p class="ci-notice ci-before">
        模型识别可选本地 Ollama 或你配置的 API；API
        模式经确认后会将待识别内容发送给所选服务。图片和文档在你确认后由 MinerU
        解析。配置和任务记录保存在本机。
      </p>
      <h3>📌 先审核，再入库</h3>
      <p class="ci-notice ci-before">
        所有任务创建都需要你确认。模型或 API 不可用时，原文仍会保存，你可以手动补充字段。
      </p>
      <h3>🗃️ 本地保存</h3>
      <p class="ci-notice">
        任务、来源与历史会在刷新和重启后保留。站内截止提醒和周 /
        月日历已可使用。图片与文档可从“我的任务 → 新建任务”上传；QQ
        连接可在上方“数据源连接”管理；备份恢复、日历自动同步及其他平台接入仍在规划中。
      </p>
    </aside>
  </div>
</template>
