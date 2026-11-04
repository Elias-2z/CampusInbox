<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import { useCampus } from '@/stores/campus'
import NewTaskModal from '@/components/NewTaskModal.vue'
import TaskEditor from '@/components/TaskEditor.vue'
import TaskAssistant from '@/components/TaskAssistant.vue'
import { browserRemindersEnabled, dispatchBrowserReminders } from '@/browserReminders'
const store = useCampus()
const route = useRoute()
const assistantOpen = ref(false)
let timer: number | undefined
let inboxTimer: number | undefined
function refreshInboxWhenVisible() {
  if (document.visibilityState === 'visible') void store.refreshInbox()
}
function refreshRemindersWhenVisible() {
  let notificationsEnabled = false
  try {
    notificationsEnabled = browserRemindersEnabled(window.localStorage)
  } catch {
    // Browser storage can be unavailable in private browsing.
  }
  if (
    (document.visibilityState === 'visible' || notificationsEnabled) &&
    !store.busy &&
    !store.reminderBusy
  ) {
    store.clock = Date.now()
    void store.refreshReminders()
  }
}
watch(
  () => store.reminderFeed,
  (feed) => {
    if (!feed?.config.enabled || !('Notification' in window)) return
    try {
      dispatchBrowserReminders(window.localStorage, window.Notification, feed.items)
    } catch {
      // In-app reminders remain available when browser storage is unavailable.
    }
  },
)
onMounted(() => {
  void store.initialize()
  inboxTimer = window.setInterval(refreshInboxWhenVisible, 5000)
  timer = window.setInterval(() => {
    store.clock = Date.now()
    refreshRemindersWhenVisible()
  }, 30000)
  document.addEventListener('visibilitychange', refreshRemindersWhenVisible)
  window.addEventListener('focus', refreshRemindersWhenVisible)
  document.addEventListener('visibilitychange', refreshInboxWhenVisible)
  window.addEventListener('focus', refreshInboxWhenVisible)
})
onUnmounted(() => {
  window.clearInterval(timer)
  window.clearInterval(inboxTimer)
  document.removeEventListener('visibilitychange', refreshRemindersWhenVisible)
  window.removeEventListener('focus', refreshRemindersWhenVisible)
  document.removeEventListener('visibilitychange', refreshInboxWhenVisible)
  window.removeEventListener('focus', refreshInboxWhenVisible)
})
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
        ><RouterLink to="/calendar" class="ci-nav" active-class="ci-nav-active">🗓️ 日历</RouterLink
        ><RouterLink to="/reminders" class="ci-nav" active-class="ci-nav-active"
          >🔔 提醒 <span class="ci-navcount">{{ store.unreadReminders.length }}</span></RouterLink
        ><RouterLink
          to="/settings"
          class="ci-nav"
          :class="{ 'ci-nav-active': route.path.startsWith('/settings') }"
          active-class="ci-nav-active"
          >⚙️ 设置</RouterLink
        >
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
      <button
        type="button"
        class="ci-btn ci-text"
        :disabled="!store.ready"
        @click="assistantOpen = true"
      >
        课务助手
      </button>
    </footer>
    <NewTaskModal v-if="store.importing" /><TaskEditor v-if="store.editorOpen" />
    <TaskAssistant v-if="assistantOpen" @close="assistantOpen = false" />
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
