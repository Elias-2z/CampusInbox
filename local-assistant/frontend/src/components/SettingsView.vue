<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { request, download, state, type ImportCapabilities } from '../api'
const settings = ref<{ ocr?: boolean; listen_host?: string; server_port?: number } | null>(null)
const error = ref('')
const notice = ref('')
const imports = ref<ImportCapabilities | null>(null)
defineEmits<{ navigate: [view: string] }>()
onMounted(async () => {
  try {
    settings.value = await request('/settings')
    imports.value = await request<ImportCapabilities>('/imports/capabilities')
  } catch (e) {
    error.value = e instanceof Error ? e.message : '读取设置失败'
  }
})
async function backup() {
  try {
    await download('/export.json', 'campusinbox-backup.json')
    notice.value = '已导出任务、通知原文和历史记录。'
  } catch (e) {
    error.value = e instanceof Error ? e.message : '导出失败'
  }
}
</script>
<template>
  <p v-if="error" role="alert" class="ci-notice">{{ error }}</p>
  <p v-if="notice" role="status" class="ci-notice">{{ notice }}</p>
  <div class="ci-settings-grid">
    <section class="ci-settings-section">
      <h2>🧠 模型与识别</h2>
      <p>{{ state?.agent.description || '规则识别与可选模型辅助' }}</p>
      <dl>
        <dt>当前模式</dt>
        <dd>{{ state?.agent.mode === 'agent' ? '规则 + 模型辅助' : '本地规则' }}</dd>
        <dt>截图 OCR</dt>
        <dd>{{ settings?.ocr ? 'Windows 本地 OCR 可用' : '当前环境未提供 Windows OCR' }}</dd>
        <dt>MinerU 附件解析</dt>
        <dd>
          {{ imports?.mineru_configured ? '已配置，上传前需确认' : '未配置，文本与手动创建仍可用' }}
        </dd>
      </dl>
      <p class="ci-muted">模型沿用项目现有配置，由副本根目录的 .env 管理；修改后重启副本服务。</p>
      <p class="ci-muted">
        图片、PDF、Word 和 PPT 解析使用 MinerU。可在根目录 .env 设置
        MINERU_API_KEY；配置后重新打开新建窗口。上传前会说明数据去向和额度。
      </p>
    </section>
    <section class="ci-settings-section">
      <h2>🔌 数据源连接</h2>
      <p>继续使用学习通同步、QQ Connector、手机通知和浏览器组件。</p>
      <div class="ci-actions">
        <button class="ci-btn ci-primary" @click="$emit('navigate', 'sync')">学习通同步</button
        ><button class="ci-btn" @click="$emit('navigate', 'mobile')">手机连接</button>
      </div>
    </section>
    <section class="ci-settings-section">
      <h2>🗃️ 数据与备份</h2>
      <p>数据保存在迁移副本的 data 目录。导出不会修改原数据。</p>
      <button class="ci-btn ci-primary" @click="backup">导出数据备份</button>
      <p class="ci-muted">
        JSON 包含任务、通知与历史；原附件、登录会话和配置不包含在此文件中。完整备份请同时保留 data
        下的数据库、uploads 目录和私有配置。
      </p>
    </section>
    <section class="ci-settings-section">
      <h2>🔔 提醒与运行</h2>
      <p>网页提醒需要页面保持打开。桌面托盘、手机提醒和微信推送沿用现有后台服务。</p>
      <div class="ci-actions">
        <button class="ci-btn" @click="$emit('navigate', 'reminders')">站内与浏览器提醒</button
        ><button class="ci-btn" @click="$emit('navigate', 'mobile')">手机提醒设置</button>
      </div>
      <p v-if="settings" class="ci-muted">
        当前服务：{{ settings.listen_host }}:{{ settings.server_port }}
      </p>
    </section>
  </div>
</template>
