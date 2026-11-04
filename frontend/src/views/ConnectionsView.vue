<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { request } from '@/api'
import type { ChaoxingStatus } from '@/chaoxing'
import { useCampus } from '@/stores/campus'
const store = useCampus()
const qq = ref<{
  adapter: { connected: boolean }
  config: { enabled: boolean; groups: unknown[] }
} | null>(null)
const busy = ref(false)
const error = ref('')
const chaoxing = ref<ChaoxingStatus | null>(null)
const chaoxingError = ref('')
async function load() {
  busy.value = true
  error.value = ''
  chaoxingError.value = ''
  try {
    const [qqResult, chaoxingResult] = await Promise.allSettled([
      request<typeof qq.value>('/connectors/qq'),
      request<ChaoxingStatus>('/connectors/chaoxing'),
    ])
    if (qqResult.status === 'fulfilled') qq.value = qqResult.value
    else error.value = store.errorMessage(qqResult.reason)
    if (chaoxingResult.status === 'fulfilled') chaoxing.value = chaoxingResult.value
    else chaoxingError.value = store.errorMessage(chaoxingResult.reason)
  } finally {
    busy.value = false
  }
}
onMounted(load)
const apps = [
  { id: 'wechat', icon: '💬', name: '微信', hint: '课程群里的通知与补充说明', tone: 'green' },
]
</script>
<template>
  <nav class="ci-connection-breadcrumb" aria-label="当前位置">
    <RouterLink to="/settings">设置</RouterLink><span>/</span><span>数据源连接</span>
  </nav>
  <div class="ci-pagehead">
    <div>
      <div class="ci-eyebrow">KEEP YOUR CAMPUS CONNECTED</div>
      <h1>把通知接进来 🔌</h1>
      <p class="ci-notice">选一个应用，管理它的账号和接收范围。</p>
    </div>
    <button class="ci-btn" :disabled="busy" @click="load">
      {{ busy ? '正在检查…' : '刷新状态' }}
    </button>
  </div>
  <p v-if="error" class="ci-inlineerror" role="alert">状态暂不可用：{{ error }}</p>
  <div class="ci-connection-grid">
    <article class="ci-connection-card">
      <div class="ci-connection-icon">🐧</div>
      <div class="ci-connection-cardhead">
        <h2>QQ</h2>
        <span
          class="ci-connection-badge"
          :class="{ 'is-online': !error && qq?.adapter.connected }"
          >{{
            error ? '状态未知' : !qq ? '读取中' : qq.adapter.connected ? '已连接' : '未连接'
          }}</span
        >
      </div>
      <p>接收选定课程群和成员的通知。</p>
      <p class="ci-connection-cardnote">
        {{
          error || !qq
            ? '进入详情查看连接配置'
            : qq.config.enabled
              ? `已开启检测 · ${qq.config.groups.length} 个群`
              : '检测已暂停'
        }}
      </p>
      <RouterLink class="ci-btn ci-primary" to="/settings/connections/qq">{{
        qq?.adapter.connected ? '管理 QQ 连接 →' : '配置 QQ 连接 →'
      }}</RouterLink>
    </article>
    <article class="ci-connection-card tone-yellow">
      <div class="ci-connection-icon">📚</div>
      <div class="ci-connection-cardhead">
        <h2>学习通</h2>
        <span
          class="ci-connection-badge"
          :class="{ 'is-online': !chaoxingError && chaoxing?.state === 'connected' }"
          >{{
            chaoxingError
              ? '状态未知'
              : !chaoxing
                ? '读取中'
                : chaoxing.state === 'connected'
                  ? '已连接'
                  : chaoxing.state === 'partial'
                    ? '部分可用'
                    : chaoxing.enabled
                      ? '正在连接或需处理'
                      : '未连接'
          }}</span
        >
      </div>
      <p>课程作业、考试和课程通知。</p>
      <p class="ci-connection-cardnote">
        {{
          chaoxingError
            ? '进入详情检查连接'
            : chaoxing?.enabled
              ? '查看采集范围与待核对消息'
              : '进入详情扫码连接'
        }}
      </p>
      <RouterLink class="ci-btn ci-primary" to="/settings/connections/chaoxing"
        >管理学习通连接 →</RouterLink
      >
    </article>
    <article
      v-for="app in apps"
      :key="app.id"
      class="ci-connection-card"
      :class="`tone-${app.tone}`"
    >
      <div class="ci-connection-icon">{{ app.icon }}</div>
      <div class="ci-connection-cardhead">
        <h2>{{ app.name }}</h2>
        <span class="ci-connection-badge">手动导入</span>
      </div>
      <p>{{ app.hint }}</p>
      <p class="ci-connection-cardnote">粘贴一条通知，送到收件箱审核</p>
      <RouterLink class="ci-btn" :to="`/settings/connections/${app.id}`">导入微信通知 →</RouterLink>
    </article>
  </div>
  <p class="ci-notice ci-connection-footnote">
    连接状态和通知接收分开显示。接入后生成的任务建议，仍会先送到收件箱供你审核。
  </p>
</template>
