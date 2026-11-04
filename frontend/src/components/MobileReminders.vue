<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { post, request } from '@/api'

type Device = { id: string; name: string; paired_at: string; last_seen: string | null }
type State = {
  enabled: boolean
  address: string | null
  devices: Device[]
  security_notice: string
}
const state = ref<State | null>(null)
const host = ref('')
const port = ref(8766)
const consent = ref(false)
const code = ref('')
const expires = ref('')
const error = ref('')
const busy = ref(false)

async function refresh() {
  try {
    state.value = await request<State>('/mobile-reminders/status')
  } catch {
    error.value = '无法读取手机提醒状态，请检查主服务。'
  }
}
async function run(action: string, body?: unknown) {
  busy.value = true
  error.value = ''
  try {
    if (action === 'pairing') {
      const result = await post<{ code: string; expires_at: string }>(
        '/mobile-reminders/pairing',
        {},
      )
      code.value = result.code
      expires.value = result.expires_at
    } else {
      await post(`/mobile-reminders/${action}`, body || {})
      if (action === 'stop') code.value = ''
    }
    await refresh()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '操作失败，请重试。'
  } finally {
    busy.value = false
  }
}
function revoke(device: Device) {
  if (window.confirm(`确定断开“${device.name}”吗？`)) void run('revoke', { device_id: device.id })
}
onMounted(() => void refresh())
</script>

<template>
  <section class="ci-form" aria-label="手机提醒">
    <h3>📱 接收电脑提醒</h3>
    <p class="ci-notice">
      配对后，Android 客户端可接收主项目的任务与截止提醒。需同一可信网络，实际送达请在手机上验证。
    </p>
    <p class="ci-notice">{{ state?.security_notice }}</p>
    <p class="ci-notice">当前：{{ state?.enabled ? state.address : '未开启' }}</p>
    <form
      v-if="!state?.enabled"
      @submit.prevent="run('start', { host, port, allow_unencrypted_lan: true })"
    >
      <label class="ci-field"
        >电脑的局域网 IPv4 地址<input
          v-model.trim="host"
          class="ci-input"
          required
          placeholder="例如 192.168.1.10"
      /></label>
      <label class="ci-field"
        >端口<input
          v-model.number="port"
          class="ci-input"
          type="number"
          min="1"
          max="65535"
          required
      /></label>
      <label class="ci-checklabel"
        ><input v-model="consent" type="checkbox" />我了解局域网连接使用明文 HTTP</label
      >
      <button class="ci-btn ci-primary" :disabled="busy || !consent || !state">开启手机提醒</button>
    </form>
    <div v-else class="ci-actions">
      <button class="ci-btn ci-primary" :disabled="busy" @click="run('pairing')">生成配对码</button>
      <button class="ci-btn" :disabled="busy" @click="run('stop')">停止提醒服务</button>
    </div>
    <p v-if="code" class="ci-notice">
      配对码：<strong>{{ code }}</strong
      >（有效期至 {{ new Date(expires).toLocaleString('zh-CN') }}）
    </p>
    <p v-if="error" class="ci-inlineerror" role="alert">{{ error }}</p>
    <ul v-if="state?.devices.length">
      <li v-for="device in state.devices" :key="device.id">
        {{ device.name }}
        <button class="ci-btn ci-text" :disabled="busy" @click="revoke(device)">断开</button>
      </li>
    </ul>
  </section>
</template>
