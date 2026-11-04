<script setup lang="ts">
import { ref } from 'vue'
import ModelSettings from '@/components/ModelSettings.vue'
import { useCampus } from '@/stores/campus'
import { downloadData } from '@/settingsExport'
import ReminderSettings from '@/components/ReminderSettings.vue'
import MobileReminders from '@/components/MobileReminders.vue'
import LegacyMigrationPreview from '@/components/LegacyMigrationPreview.vue'
const store = useCampus()
const exporting = ref<'json' | 'txt' | null>(null)
const exportNotice = ref('')
const exportError = ref('')
async function exportData(format: 'json' | 'txt') {
  if (exporting.value) return
  exporting.value = format
  exportNotice.value = ''
  exportError.value = ''
  try {
    await downloadData(format)
    exportNotice.value = '文件已生成，请查看下载。导出不会修改现有数据。'
  } catch (e) {
    exportError.value = store.errorMessage(e)
  } finally {
    exporting.value = null
  }
}
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
  <MobileReminders />
  <ReminderSettings />
  <LegacyMigrationPreview />
  <div class="ci-settingslayout">
    <ModelSettings />
    <aside class="ci-source">
      <h3>🔒 本地识别与文件解析</h3>
      <p class="ci-notice ci-before">
        手动导入的通知可使用已授权的模型 API。QQ、学习通自动采集和课务助手使用本机
        Ollama。图片和文档在你确认后可由 MinerU 解析。
      </p>
      <h3>📌 先审核，再入库</h3>
      <p class="ci-notice ci-before">
        所有任务创建都需要你确认。模型不可用时，原文仍会保存，你可以手动补充字段。
      </p>
      <h3>🗃️ 本地保存</h3>
      <p class="ci-notice">
        任务、来源与历史会在刷新和重启后保留。站内截止提醒和周 /
        月日历已可使用。图片与文档可从“我的任务 → 新建任务”上传；日历自动同步将在后续阶段提供。
      </p>
    </aside>
  </div>
  <section class="ci-form ci-reminder-settings" aria-label="数据导出">
    <h3>🗃️ 导出数据</h3>
    <p class="ci-notice ci-before">
      可读报告汇总任务、建议、历史和通知原文；JSON 还包含草稿、变更、提醒、附件提取文字和 QQ /
      学习通队列记录。 导出不会修改现有数据。
    </p>
    <div class="ci-actions">
      <button
        type="button"
        class="ci-btn ci-primary cursor-interaction"
        :disabled="!!exporting"
        @click="exportData('txt')"
      >
        {{ exporting === 'txt' ? '正在导出…' : '导出可读报告' }}
      </button>
      <button
        type="button"
        class="ci-btn cursor-interaction"
        :disabled="!!exporting"
        @click="exportData('json')"
      >
        {{ exporting === 'json' ? '正在导出…' : '导出 JSON 记录' }}
      </button>
    </div>
    <p class="ci-notice ci-before">
      导出不包含附件原文件、模型或连接设置、凭据及学习通链接和未知字段。目前没有 JSON
      导入恢复功能，不能替代数据库、附件目录和所需私有配置的备份。
      原文可能包含个人信息或自行输入的敏感内容，请谨慎保管。
    </p>
    <p v-if="exportNotice" class="ci-status" role="status">{{ exportNotice }}</p>
    <p v-if="exportError" class="ci-inlineerror" role="alert">{{ exportError }}</p>
  </section>
</template>
