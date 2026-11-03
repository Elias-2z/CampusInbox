<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import QQConnections from '@/components/QQConnections.vue'
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
  <QQConnections v-if="provider === 'qq'" />
  <section v-else class="ci-form ci-connection-empty">
    <span class="ci-connection-icon">{{ provider === 'chaoxing' ? '📚' : '💬' }}</span>
    <h2>{{ name }}暂未接入主应用</h2>
    <p class="ci-notice">
      连接功能开放后，你可以在这里单独配置。当前可以先粘贴通知或上传截图、文件，继续整理任务。
    </p>
    <RouterLink class="ci-btn ci-primary" to="/tasks">去导入通知 →</RouterLink>
  </section>
</template>
