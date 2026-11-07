<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { request, type ReminderConfig } from '@/api'
import { useCampus } from '@/stores/campus'
import {
  browserRemindersEnabled,
  disableBrowserReminders,
  enableBrowserReminders,
} from '@/browserReminders'
const store = useCampus()
const config = ref<ReminderConfig>({ enabled: true, lead_minutes: 1440, overdue_enabled: true })
const loaded = ref(false)
const working = ref(false)
const error = ref('')
const browserEnabled = ref(false)
const browserMessage = ref('')
const leadOptions = [
  { value: 15, label: '提前 15 分钟' },
  { value: 60, label: '提前 1 小时' },
  { value: 180, label: '提前 3 小时' },
  { value: 360, label: '提前 6 小时' },
  { value: 1440, label: '提前 1 天' },
  { value: 2880, label: '提前 2 天' },
  { value: 10080, label: '提前 1 周' },
]
async function load() {
  error.value = ''
  try {
    config.value = await request<ReminderConfig>('/settings/reminders')
    loaded.value = true
  } catch (e) {
    error.value = store.errorMessage(e)
  }
}
onMounted(() => {
  void load()
  try {
    browserEnabled.value = browserRemindersEnabled(window.localStorage)
  } catch {
    browserMessage.value = '浏览器存储不可用，无法开启浏览器通知。'
  }
})
async function enableBrowser() {
  browserMessage.value = ''
  if (!('Notification' in window)) {
    browserMessage.value = '当前浏览器不支持通知。'
    return
  }
  if (!store.reminderFeed || store.reminderStale) {
    browserMessage.value = '请先刷新站内提醒，再开启浏览器通知。'
    return
  }
  try {
    browserEnabled.value = await enableBrowserReminders(
      window.localStorage,
      window.Notification,
      store.reminderFeed.items,
    )
    browserMessage.value = browserEnabled.value
      ? '已开启；已有未读提醒不会集中弹出。'
      : '未获得通知权限，请在浏览器设置中允许。'
  } catch {
    browserMessage.value = '无法开启浏览器通知，请检查权限和浏览器存储。'
  }
}
function disableBrowser() {
  try {
    disableBrowserReminders(window.localStorage)
    browserEnabled.value = false
    browserMessage.value = '已关闭浏览器通知。'
  } catch {
    browserMessage.value = '无法保存设置，请检查浏览器存储。'
  }
}
async function save() {
  working.value = true
  error.value = ''
  try {
    config.value = await request<ReminderConfig>('/settings/reminders', {
      method: 'PUT',
      body: JSON.stringify(config.value),
    })
    await store.refreshReminders()
    store.notice = '站内提醒设置已保存。'
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    working.value = false
  }
}
</script>
<template>
  <form class="ci-form ci-reminder-settings" aria-label="截止提醒设置" @submit.prevent="save">
    <h3>🔔 给截止时间留点余地</h3>
    <p class="ci-notice ci-before">页面打开时检查提醒，约每 30 秒刷新。关闭页面后不会弹出通知。</p>
    <fieldset class="ci-fieldset" :disabled="!loaded || working">
      <label class="ci-checklabel"
        ><input v-model="config.enabled" type="checkbox" />启用站内截止提醒</label
      >
      <div class="ci-fieldrow">
        <label class="ci-field"
          >提前多久提醒？<select v-model.number="config.lead_minutes" class="ci-input" required>
            <option v-for="option in leadOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
            <option
              v-if="!leadOptions.some((option) => option.value === config.lead_minutes)"
              :value="config.lead_minutes"
            >
              提前 {{ config.lead_minutes }} 分钟
            </option></select
          ><small class="ci-evidence">按每项任务最新的截止时间计算。</small></label
        >
        <label class="ci-checklabel"
          ><input v-model="config.overdue_enabled" type="checkbox" />允许发送逾期通知</label
        >
      </div>
      <button class="ci-btn ci-primary cursor-interaction">
        {{ working ? '正在保存…' : '保存提醒设置 ✓' }}
      </button>
    </fieldset>
    <div class="ci-actions">
      <button v-if="!browserEnabled" type="button" class="ci-btn" @click="enableBrowser">
        开启浏览器通知
      </button>
      <button v-else type="button" class="ci-btn" @click="disableBrowser">关闭浏览器通知</button>
    </div>
    <p class="ci-notice ci-before">
      浏览器通知需要授权；页面保持打开时才会检查，系统节能设置可能延迟提醒。
    </p>
    <p v-if="browserMessage" class="ci-notice" role="status">{{ browserMessage }}</p>
    <p class="ci-notice ci-before">
      提醒待办、进行中和已做完但未提交的任务。标为已读不会完成任务；提交、取消、归档或修改截止时间后，提醒会按最新任务重新计算。
    </p>
    <div v-if="error" class="ci-inlineerror" role="alert">
      {{ error
      }}<button
        v-if="!loaded"
        class="ci-btn ci-text cursor-interaction"
        type="button"
        @click="load"
      >
        重新读取
      </button>
    </div>
  </form>
</template>
