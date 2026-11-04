<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request, post, type ModelConfig, type Probe } from '@/api'
import { useCampus } from '@/stores/campus'
interface APIConfig extends ModelConfig {
  consent: boolean
  daily_limit: number
  max_tokens: number
  json_mode: boolean
  key_saved: boolean
}
interface Inference {
  mode: 'local' | 'api'
  revision: number
  local: ModelConfig
  api: APIConfig
  usage: { used: number; limit: number }
  credential_storage: string
  credential_error?: string
}
const store = useCampus()
const config = ref<Inference | null>(null)
const apiKey = ref('')
const savedURL = ref('')
const activeMode = ref('')
const busy = ref(false)
const error = ref('')
const probe = ref<Probe | null>(null)
const localMode = computed(() => config.value?.mode === 'local')
const keyAvailable = computed(
  () => !!config.value?.api.key_saved && config.value.api.base_url === savedURL.value,
)
async function load() {
  busy.value = true
  error.value = ''
  try {
    config.value = await request<Inference>('/settings/inference')
    savedURL.value = config.value.api.base_url
    activeMode.value = config.value.mode
    apiKey.value = ''
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    busy.value = false
  }
}
onMounted(load)
function payload() {
  if (!config.value) throw new Error('设置尚未读取')
  const api = config.value.api
  return {
    mode: config.value.mode,
    revision: config.value.revision,
    local: config.value.local,
    api: {
      enabled: api.enabled,
      base_url: api.base_url,
      model: api.model,
      timeout_seconds: api.timeout_seconds,
      consent: api.consent,
      daily_limit: api.daily_limit,
      max_tokens: api.max_tokens,
      json_mode: api.json_mode,
      api_key: apiKey.value,
    },
  }
}
function selectMode(mode: 'local' | 'api') {
  if (config.value) config.value.mode = mode
  probe.value = null
}
function changeModel() {
  if (config.value) config.value.api.consent = false
  probe.value = null
}
function changeDestination() {
  if (config.value) config.value.api.consent = false
  probe.value = null
  apiKey.value = ''
}
async function save() {
  busy.value = true
  error.value = ''
  try {
    config.value = await request<Inference>('/settings/inference', {
      method: 'PUT',
      body: JSON.stringify(payload()),
    })
    savedURL.value = config.value.api.base_url
    activeMode.value = config.value.mode
    apiKey.value = ''
    probe.value = null
    store.notice = '识别方式已保存，新导入和重新识别时生效。'
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    busy.value = false
  }
}
async function test() {
  busy.value = true
  error.value = ''
  probe.value = null
  try {
    probe.value = await post<Probe>('/settings/inference/probe', payload())
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <form class="ci-form" aria-label="模型与 API 设置" @submit.prevent="save">
    <h3>🧠 通知识别</h3>
    <p class="ci-notice ci-before">
      规则先处理明确的内容，需要时再由你选择的模型补充。所有结果仍需审核。
    </p>
    <p v-if="activeMode" class="ci-notice">
      当前生效：{{ activeMode === 'local' ? '本地 Ollama' : '用户配置的 API' }}。修改后保存才生效。
    </p>
    <fieldset class="ci-fieldset" :disabled="busy || !config">
      <template v-if="config">
        <div class="ci-create-modes" role="group" aria-label="选择模型运行方式">
          <button
            type="button"
            class="ci-create-mode"
            :aria-pressed="localMode"
            @click="selectMode('local')"
          >
            <span>💻</span><b>本地模型</b><small>使用本机 Ollama</small>
          </button>
          <button
            type="button"
            class="ci-create-mode"
            :aria-pressed="!localMode"
            @click="selectMode('api')"
          >
            <span>🔑</span><b>API 模型</b><small>使用你填写的服务</small>
          </button>
        </div>
        <template v-if="localMode">
          <label class="ci-checklabel"
            ><input v-model="config.local.enabled" type="checkbox" />启用本地模型兜底</label
          >
          <label class="ci-field"
            >Ollama 地址<input v-model="config.local.base_url" class="ci-input" type="url" required
          /></label>
          <label class="ci-field"
            >已安装的模型<input
              v-model="config.local.model"
              class="ci-input"
              list="ci-models"
              maxlength="200"
              :required="config.local.enabled"
              placeholder="先测试连接，读取本机模型" /><datalist id="ci-models">
              <option v-for="name in probe?.models || []" :key="name" :value="name" /></datalist
          ></label>
          <label class="ci-field"
            >超时（秒）<input
              v-model.number="config.local.timeout_seconds"
              class="ci-input"
              type="number"
              min="3"
              max="120"
              required
          /></label>
          <p class="ci-notice">本地模型不可用时保留规则结果，不自动转到 API。</p>
        </template>
        <template v-else>
          <label class="ci-checklabel"
            ><input v-model="config.api.enabled" type="checkbox" />启用 API 模型兜底</label
          >
          <label class="ci-field"
            >API 基础地址<input
              v-model="config.api.base_url"
              class="ci-input"
              type="url"
              required
              placeholder="服务商提供的 HTTPS Base URL，通常以 /v1 结尾"
              @input="changeDestination"
            /><small class="ci-evidence"
              >支持兼容 Chat Completions 的服务；不要填写完整 /chat/completions 地址。</small
            ></label
          >
          <label class="ci-field"
            >Model ID<input
              v-model="config.api.model"
              class="ci-input"
              required
              maxlength="200"
              placeholder="填写该服务实际支持的模型 ID"
              @input="changeModel"
          /></label>
          <label class="ci-field"
            >API Key<input
              v-model="apiKey"
              class="ci-input"
              type="password"
              autocomplete="new-password"
              maxlength="4096"
              :placeholder="
                keyAvailable ? '已安全保存；留空保持原值' : '只在本机安全存储，不会回显'
              "
          /></label>
          <p class="ci-notice">
            配置保存在本机；密钥保存到{{
              config.credential_storage
            }}，不写入普通数据库、浏览器存储或 GitHub。
          </p>
          <div class="ci-fieldrow">
            <label class="ci-field"
              >超时（秒）<input
                v-model.number="config.api.timeout_seconds"
                class="ci-input"
                type="number"
                min="3"
                max="120"
                required
            /></label>
            <label class="ci-field"
              >每日调用上限<input
                v-model.number="config.api.daily_limit"
                class="ci-input"
                type="number"
                min="1"
                max="10000"
                required
            /></label>
          </div>
          <details class="ci-morefields">
            <summary>API 输出选项</summary>
            <label class="ci-checklabel"
              ><input v-model="config.api.json_mode" type="checkbox" />请求 JSON
              输出模式（模型需支持）</label
            >
            <label class="ci-field"
              >每次最大输出 Token<input
                v-model.number="config.api.max_tokens"
                class="ci-input"
                type="number"
                min="128"
                max="4000"
                required
            /></label>
          </details>
          <p class="ci-notice ci-before">
            本机已保存地址今日调用尝试：{{ config.usage.used }} 次，按 UTC
            日期统计，测试和失败请求也计入。本机次数上限不等于服务商费用上限。
          </p>
          <label class="ci-checklabel"
            ><input
              v-model="config.api.consent"
              type="checkbox"
              :required="config.api.enabled"
            />我确认：启用 API
            后，需要模型识别的通知原文、群名、发件人显示名和发送时间会发送到上述服务，可能产生费用。</label
          >
          <p class="ci-notice">
            测试仅发送固定的合成文本，不发送已有通知；测试也可能消耗少量额度。更换地址或模型后需重新确认。
          </p>
        </template>
        <div class="ci-actions ci-before">
          <button class="ci-btn ci-primary">{{ busy ? '处理中…' : '保存识别设置 ✓' }}</button>
          <button
            type="button"
            class="ci-btn"
            :disabled="!localMode && !config.api.consent"
            @click="test"
          >
            {{ localMode ? '测试连接 / 读取模型' : '测试 API（合成文本）' }}
          </button>
        </div>
      </template>
    </fieldset>
    <p v-if="!config && !busy" class="ci-notice">
      <button type="button" class="ci-btn" @click="load">重新读取设置</button>
    </p>
    <div v-if="error || config?.credential_error" class="ci-inlineerror" role="alert">
      {{ error || config?.credential_error }}
    </div>
    <div v-if="probe" class="ci-proberesult" role="status">
      <b>{{ probe.reachable ? '✅ 检查通过' : '暂时连接不上' }}</b>
      <p v-if="probe.error">{{ probe.error }}</p>
      <p v-else-if="localMode">已安装：{{ probe.models.join('、') || '暂无模型' }}</p>
    </div>
  </form>
</template>
