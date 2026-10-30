<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { post, request, type ModelConfig, type Probe } from '@/api'
import { useCampus } from '@/stores/campus'
import ReminderSettings from '@/components/ReminderSettings.vue'
const store = useCampus()
const config = ref<ModelConfig>({
  enabled: false,
  base_url: 'http://127.0.0.1:11434',
  model: '',
  timeout_seconds: 45,
})
const probe = ref<Probe | null>(null)
const working = ref(false)
const loaded = ref(false)
const error = ref('')
onMounted(async () => {
  try {
    config.value = await request<ModelConfig>('/settings/model')
    loaded.value = true
  } catch (e) {
    error.value = store.errorMessage(e)
  }
})
async function testConnection() {
  working.value = true
  error.value = ''
  probe.value = null
  try {
    probe.value = await post<Probe>('/settings/model/probe', config.value)
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    working.value = false
  }
}
async function save() {
  working.value = true
  error.value = ''
  try {
    config.value = await request<ModelConfig>('/settings/model', {
      method: 'PUT',
      body: JSON.stringify(config.value),
    })
    store.notice = '本地模型设置已保存，新导入或重新识别时生效。'
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    working.value = false
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
  <ReminderSettings />
  <div class="ci-settingslayout">
    <form class="ci-form" @submit.prevent="save">
      <h3>🧠 本地模型兜底</h3>
      <p class="ci-notice ci-before">
        规则能明确识别的通知会直接跳过模型。模糊信息可以交给本机 Ollama 帮忙，最后仍由你确认。
      </p>
      <fieldset class="ci-fieldset" :disabled="working || !loaded">
        <label class="ci-checklabel"
          ><input v-model="config.enabled" type="checkbox" />启用本地模型兜底</label
        ><label class="ci-field"
          >Ollama 地址<input
            v-model="config.base_url"
            class="ci-input"
            type="url"
            required /></label
        ><label class="ci-field"
          >模型名称<input
            v-model="config.model"
            class="ci-input"
            list="ci-models"
            maxlength="200"
            placeholder="填写已安装的模型名，或先测试连接"
            :required="config.enabled" /><datalist id="ci-models">
            <option
              v-for="name in probe?.models || []"
              :key="name"
              :value="name"
            /></datalist></label
        ><label class="ci-field"
          >超时（秒）<input
            v-model.number="config.timeout_seconds"
            class="ci-input"
            type="number"
            min="3"
            max="120"
            required
        /></label>
        <div class="ci-actions">
          <button class="ci-btn ci-primary cursor-interaction">
            {{ working ? '处理中…' : '保存设置 ✓' }}</button
          ><button type="button" class="ci-btn cursor-interaction" @click="testConnection">
            测试连接 / 读取模型
          </button>
        </div>
      </fieldset>
      <div v-if="error" class="ci-inlineerror" role="alert">{{ error }}</div>
      <div v-if="probe" class="ci-proberesult" role="status">
        <template v-if="probe.reachable"
          ><b>✅ Ollama 可连接</b>
          <p v-if="probe.models.length">已安装：{{ probe.models.join('、') }}</p>
          <p v-else>还没有可用模型，请先在 Ollama 中安装模型，再测试连接。</p></template
        ><template v-else
          >暂时连接不上。请先启动 Ollama，再测试一次。
          <p>{{ probe.error }}</p></template
        >
      </div>
    </form>
    <aside class="ci-source">
      <h3>🔒 信息留在本机</h3>
      <p class="ci-notice ci-before">第一版只接受本机 Ollama 地址。不会自动把通知发送到云端。</p>
      <h3>📌 先审核，再入库</h3>
      <p class="ci-notice ci-before">
        所有任务创建都需要你确认。模型不可用时，原文仍会保存，你可以手动补充字段。
      </p>
      <h3>🗃️ 本地保存</h3>
      <p class="ci-notice">
        任务、来源与历史会在刷新和重启后保留。站内截止提醒已可使用；备份恢复、OCR、完整日历和平台连接将在后续阶段提供。
      </p>
    </aside>
  </div>
</template>
