<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import QQConnections from '@/components/QQConnections.vue'
import ChaoxingConnection from '@/components/ChaoxingConnection.vue'
import WechatPush from '@/components/WechatPush.vue'
import { useCampus } from '@/stores/campus'
const store = useCampus()
const route = useRoute()
const provider = computed(() => String(route.params.provider))
const name = computed(
  () => ({ qq: 'QQ', chaoxing: '学习通', wechat: '微信' })[provider.value] || '数据源',
)
</script>
<template>
  <nav class="ci-connection-breadcrumb" aria-label="当前位置">
    <RouterLink to="/settings">设置</RouterLink><span>/</span
    ><RouterLink to="/settings/connections">数据源连接</RouterLink><span>/</span
    ><span>{{ name }}</span>
  </nav>
  <div class="ci-pagehead">
    <div>
      <div class="ci-eyebrow">YOUR SOURCES, YOUR CHOICE</div>
      <h1>{{ name }} 连接</h1>
      <p class="ci-notice">在这里管理这个应用的账号、通知范围和连接配置。</p>
    </div>
    <RouterLink class="ci-btn" to="/settings/connections">返回全部连接</RouterLink>
  </div>
  <template v-if="provider === 'qq'">
    <button type="button" class="ci-btn" @click="store.openImport('QQ')">粘贴 QQ 通知</button>
    <QQConnections />
  </template>
  <ChaoxingConnection v-else-if="provider === 'chaoxing'" />
  <section v-else class="ci-form ci-connection-empty">
    <span class="ci-connection-icon">💬</span>
    <h2>粘贴微信通知</h2>
    <p class="ci-notice">
      从微信复制一条通知，填写原消息发送时间。整理后的内容会进入收件箱，核对后再创建或更新任务。
    </p>
    <button type="button" class="ci-btn ci-primary" @click="store.openImport('微信')">
      开始粘贴
    </button>
  </section>
  <WechatPush v-if="provider === 'wechat'" />
</template>
