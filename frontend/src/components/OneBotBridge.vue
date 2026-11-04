<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { post, request } from '@/api'
import { useCampus } from '@/stores/campus'
const emit = defineEmits<{ changed: [] }>()
const panel = ref<HTMLDetailsElement>()
defineExpose({
  open: () => {
    if (panel.value) {
      panel.value.open = true
      panel.value.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  },
})
const store = useCampus()
interface Bridge {
  enabled: boolean
  platform: string
  credential_storage: string
  credential_supported: boolean
  base_url: string
  token_saved: boolean
  event_secret_saved: boolean
  state: { connected: boolean; account_id: string; name: string }
  error: string
  callback_url: string
}
const info = ref<Bridge | null>(null)
const enabled = ref(false)
const url = ref('http://127.0.0.1:3000')
const token = ref('')
const secret = ref('')
const busy = ref(false)
const error = ref('')
onMounted(async () => {
  try {
    info.value = await request<Bridge>('/connectors/qq/onebot')
    enabled.value = info.value.enabled
    url.value = info.value.base_url
  } catch (e) {
    error.value = store.errorMessage(e)
  }
})
async function act(probe = false) {
  busy.value = true
  error.value = ''
  try {
    info.value = probe
      ? await post<Bridge>('/connectors/qq/onebot/probe')
      : await request<Bridge>('/connectors/qq/onebot', {
          method: 'PUT',
          body: JSON.stringify({
            enabled: enabled.value,
            base_url: url.value,
            access_token: token.value,
            event_secret: secret.value,
          }),
        })
    token.value = ''
    secret.value = ''
    store.notice = probe
      ? '消息桥连接检查已完成。'
      : `桥接配置已保存，密钥保存在${info.value.credential_storage}。`
    emit('changed')
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <details ref="panel" class="ci-source ci-before">
    <summary>⚙️ 高级连接配置</summary>
    <p class="ci-notice ci-before">
      先在本机 NapCat 中完成 QQ 登录，启用 OneBot HTTP 服务和 HTTP
      事件上报。这里连接消息桥，不安装或修改 QQ。Windows 与 macOS 均使用各自的系统安全凭据存储。
    </p>
    <p v-if="info" class="ci-notice">凭据存储：{{ info.credential_storage }}</p>
    <p v-if="info && !info.credential_supported" class="ci-inlineerror" role="alert">
      当前系统尚未提供 QQ 安全凭据存储，请使用 Windows 或 macOS。
    </p>
    <form @submit.prevent="act()">
      <fieldset class="ci-fieldset" :disabled="busy || !info || !info.credential_supported">
        <label class="ci-field"
          >OneBot HTTP 地址<input
            v-model="url"
            class="ci-input"
            type="url"
            required
            placeholder="http://127.0.0.1:3000"
        /></label>
        <label class="ci-field"
          >OneBot Access Token（不是 NapCat WebUI 密码）<input
            v-model="token"
            class="ci-input"
            type="password"
            autocomplete="new-password"
            :placeholder="
              info?.token_saved ? '已安全保存；留空保持原值' : '至少 24 位，与消息桥保持一致'
            "
        /></label>
        <label class="ci-field"
          >HTTP 事件签名 Secret<input
            v-model="secret"
            class="ci-input"
            type="password"
            autocomplete="new-password"
            :placeholder="
              info?.event_secret_saved
                ? '已安全保存；留空保持原值'
                : '至少 24 位，与事件上报 Secret 保持一致'
            "
        /></label>
        <p class="ci-notice">
          密钥不会回显或写入数据库与普通配置文件。配置文件只保存本机地址和启用状态。
        </p>
        <label class="ci-checklabel"
          ><input v-model="enabled" type="checkbox" />启用本机消息桥连接</label
        >
        <div class="ci-actions">
          <button class="ci-btn ci-primary">保存桥接配置</button>
          <button type="button" class="ci-btn" :disabled="!info?.enabled" @click="act(true)">
            检查已保存的连接
          </button>
        </div>
      </fieldset>
    </form>
    <p v-if="info" class="ci-notice ci-before">
      状态：{{ info.state.name }}{{ info.state.connected ? `（QQ ${info.state.account_id}）` : '' }}
    </p>
    <label v-if="info" class="ci-field"
      >消息桥 HTTP 事件上报地址<input class="ci-input" readonly :value="info.callback_url"
    /></label>
    <p class="ci-notice">
      群与成员列表读取成功不代表消息上报正常。保存下方检测范围后，在测试群发送一条通知，检查最近接收时间及收件箱；图片与文件需人工核对，不会自动下载。
    </p>
    <p v-if="error || info?.error" class="ci-inlineerror" role="alert">
      {{ error || info?.error }}
    </p>
  </details>
</template>
