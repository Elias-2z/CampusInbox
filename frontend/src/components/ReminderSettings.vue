<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { request, type ReminderConfig } from '@/api'
import { useCampus } from '@/stores/campus'
const store = useCampus()
const config = ref<ReminderConfig>({ enabled: true, lead_minutes: 1440, overdue_enabled: true })
const loaded = ref(false)
const working = ref(false)
const error = ref('')
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
onMounted(load)
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
    <p class="ci-notice ci-before">
      页面打开时检查站内提醒，约每 30 秒刷新，回到页面会重新检查。关闭页面后不会弹出系统通知。
    </p>
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
          ><input v-model="config.overdue_enabled" type="checkbox" />显示逾期提醒</label
        >
      </div>
      <button class="ci-btn ci-primary cursor-interaction">
        {{ working ? '正在保存…' : '保存提醒设置 ✓' }}
      </button>
    </fieldset>
    <p class="ci-notice ci-before">
      只提醒待办和进行中的任务。标为已读不会完成任务；完成、取消、归档或修改截止时间后，提醒会按最新任务重新计算。
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
