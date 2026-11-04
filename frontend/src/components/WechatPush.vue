<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { post, request } from '@/api'

interface PushStatus {
  supported: boolean
  configured: boolean
  enabled: boolean
  include_notices?: boolean
  include_expanded_notices?: boolean
  include_review_results?: boolean
  result: string
}

const path = '/connectors/wechat/push'
const status = ref<PushStatus>()
const token = ref('')
const enabled = ref(false)
const includeNotices = ref(false)
const includeExpandedNotices = ref(false)
const includeReviewResults = ref(false)
const busy = ref(false)
const error = ref('')
const results: Record<string, string> = {
  none: '暂无发送记录',
  sending: '发送处理中',
  accepted: '服务已受理，实际送达请在微信核对',
  rejected: '服务拒绝请求，推送已暂停',
  unknown: '发送结果不明，推送已暂停',
  credential_error: '本机无法读取凭据，推送已暂停',
}

async function run(operation: () => Promise<PushStatus>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await operation()
    status.value = result
    enabled.value = result.enabled
    includeNotices.value = result.include_notices ?? false
    includeExpandedNotices.value = result.include_expanded_notices ?? false
    includeReviewResults.value = result.include_review_results ?? false
    token.value = ''
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '操作失败，请重试。'
  } finally {
    busy.value = false
  }
}

function save(clear = false) {
  return run(() =>
    request<PushStatus>(path, {
      method: 'PUT',
      body: JSON.stringify(
        clear
          ? { enabled: false, clear_token: true }
          : {
              enabled: enabled.value,
              token: token.value.trim() || undefined,
              include_notices: includeNotices.value,
              include_expanded_notices: includeExpandedNotices.value,
              include_review_results: includeReviewResults.value,
            },
      ),
    }),
  )
}

onMounted(() => run(() => request<PushStatus>(path)))
</script>

<template>
  <section class="ci-form ci-connection-empty" aria-label="微信截止提醒">
    <h2>微信截止提醒</h2>
    <p class="ci-notice">
      开启后，主项目运行时通过 PushPlus 发送新出现的未读截止提醒。服务只会收到 Token
      和通用提醒文字。
    </p>
    <p v-if="status && !status.supported" class="ci-notice">此功能需要 Windows 本机凭据加密。</p>
    <form v-if="status?.supported" class="ci-form" @submit.prevent="save()">
      <label class="ci-field">
        PushPlus Token
        <input
          v-model="token"
          class="ci-input"
          type="password"
          autocomplete="new-password"
          maxlength="256"
          :placeholder="status.configured ? '已加密保存，留空保留' : '输入 Token'"
          :disabled="busy"
        />
      </label>
      <label>
        <input v-model="enabled" type="checkbox" :disabled="busy" />
        启用微信截止提醒
      </label>
      <label>
        <input v-model="includeNotices" type="checkbox" :disabled="busy || !enabled" />
        同时提醒新发布的学习通课程通知
      </label>
      <label>
        <input v-model="includeExpandedNotices" type="checkbox" :disabled="busy || !enabled" />
        同时提醒学习通新聊天、新作业考试和待核对的任务变更
      </label>
      <label>
        <input v-model="includeReviewResults" type="checkbox" :disabled="busy || !enabled" />
        同时提醒学习通任务审核结果
      </label>
      <div class="ci-actions">
        <button type="submit" class="ci-btn ci-primary" :disabled="busy">保存推送设置</button>
        <button
          type="button"
          class="ci-btn"
          :disabled="busy || !status.configured"
          @click="run(() => post<PushStatus>(path + '/test'))"
        >
          发送测试提醒
        </button>
        <button
          type="button"
          class="ci-btn ci-text"
          :disabled="busy || !status.configured"
          @click="save(true)"
        >
          关闭并清除 Token
        </button>
        <button
          type="button"
          class="ci-btn ci-text"
          :disabled="busy"
          @click="run(() => request(path))"
        >
          刷新推送状态
        </button>
      </div>
    </form>
    <p v-if="status?.supported" role="status">{{ results[status.result] }}</p>
    <p v-if="error" class="ci-inlineerror" role="alert">{{ error }}</p>
  </section>
</template>
