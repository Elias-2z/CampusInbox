<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { RouterView } from 'vue-router'
import { useCampus } from '@/stores/campus'
import ImportModal from '@/components/ImportModal.vue'
import TaskEditor from '@/components/TaskEditor.vue'
const store = useCampus()
let timer: number | undefined
onMounted(() => {
  void store.initialize()
  timer = window.setInterval(() => {
    store.clock = Date.now()
  }, 30000)
})
onUnmounted(() => window.clearInterval(timer))
</script>
<template>
  <div class="app-root">
    <header class="ci-top">
      <RouterLink to="/" class="ci-brand"
        ><span class="ci-mark" aria-hidden="true">↗</span>CampusInbox<span class="ci-branddot"
          >.</span
        ></RouterLink
      >
      <nav aria-label="主导航">
        <RouterLink to="/" class="ci-nav" exact-active-class="ci-nav-active">☀️ 今天</RouterLink
        ><RouterLink to="/inbox" class="ci-nav" active-class="ci-nav-active"
          >📮 收件箱 <span class="ci-navcount">{{ store.pending.length }}</span></RouterLink
        ><RouterLink to="/tasks" class="ci-nav" active-class="ci-nav-active">🗂️ 我的任务</RouterLink
        ><RouterLink to="/settings" class="ci-nav" active-class="ci-nav-active">⚙️ 设置</RouterLink>
      </nav>
      <span class="ci-demo">LESS CHAOS, MORE CAMPUS</span>
    </header>
    <div
      v-if="store.error && !store.importing && !store.editorOpen"
      class="ci-status ci-errorbanner"
      role="alert"
    >
      <span>{{ store.error }}</span
      ><button
        class="ci-btn ci-text cursor-interaction"
        :disabled="store.loading"
        @click="store.initialize"
      >
        刷新 ↻
      </button>
    </div>
    <div v-if="store.notice" class="ci-status" role="status" aria-live="polite">
      <span>{{ store.notice }}</span
      ><button
        v-if="store.canUndo"
        class="ci-btn ci-text cursor-interaction"
        :disabled="store.busy"
        @click="store.undo"
      >
        撤销 ↶</button
      ><button
        class="ci-dismiss cursor-interaction"
        aria-label="关闭提示"
        @click="store.notice = ''"
      >
        ×
      </button>
    </div>
    <p v-if="store.loading && !store.ready" class="ci-loading" role="status">
      🌱 正在打开你的校园清单…
    </p>
    <main class="ci-main"><RouterView /></main>
    <footer class="ci-footer">
      <span>✳ 让分散的校园信息，变成可执行任务。</span><span>本地保存 · 确认后创建</span>
    </footer>
    <ImportModal v-if="store.importing" /><TaskEditor v-if="store.editorOpen" />
    <div v-if="store.celebration" class="ci-celebration" aria-hidden="true">
      <span
        v-for="(emoji, index) in ['✨', '🎉', '🌟', '💜', '✳️']"
        :key="index"
        class="ci-particle"
        :style="{ '--x': `${20 + index * 13}%` }"
        >{{ emoji }}</span
      >
    </div>
  </div>
</template>
