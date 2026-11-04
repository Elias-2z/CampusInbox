<script setup lang="ts">
import { computed, ref } from 'vue'
import { displayDate, displayDeadline } from '@/api'
import { useCampus } from '@/stores/campus'
const store = useCampus()
const tab = ref('unread')
const snoozeMinutes = ref(30)
const tabs = [
  { id: 'unread', label: '未读' },
  { id: 'snoozed', label: '稍后提醒' },
  { id: 'read', label: '已读' },
  { id: 'all', label: '全部' },
]
const items = computed(
  () =>
    store.reminderFeed?.items.filter((item) => tab.value === 'all' || item.status === tab.value) ||
    [],
)
</script>
<template>
  <div class="ci-pagehead">
    <div>
      <div class="ci-eyebrow">A NUDGE, RIGHT ON TIME</div>
      <h1>别让截止时间偷袭你 🔔</h1>
    </div>
    <button
      class="ci-btn cursor-interaction"
      :disabled="store.reminderLoading"
      @click="store.refreshReminders"
    >
      {{ store.reminderLoading ? '正在检查…' : '检查提醒 ↻' }}
    </button>
  </div>
  <p class="ci-notice ci-before">
    页面打开时自动检查，可在设置中开启浏览器通知。已读不等于任务已完成，关闭页面后不会弹出通知。
  </p>
  <div v-if="store.reminderError" class="ci-status ci-errorbanner" role="alert">
    {{ store.reminderError
    }}<span v-if="store.reminderStale && store.reminderFeed"
      >当前展示上次检查结果，请重新检查。</span
    >
  </div>
  <section v-if="store.reminderFeed && !store.reminderFeed.config.enabled" class="ci-first">
    <span class="ci-firstemoji">🔕</span>
    <h2>提醒暂时关着</h2>
    <p>启用后，会按最新任务检查截止时间。</p>
    <RouterLink class="ci-btn ci-primary cursor-interaction" to="/settings"
      >去设置提醒 →</RouterLink
    >
  </section>
  <template v-else>
    <div class="ci-filters" aria-label="提醒状态">
      <button
        v-for="item in tabs"
        :key="item.id"
        class="ci-filter cursor-interaction"
        :aria-pressed="tab === item.id"
        @click="tab = item.id"
      >
        {{ item.label
        }}<span v-if="item.id === 'unread'"> · {{ store.unreadReminders.length }}</span>
      </button>
    </div>
    <div class="ci-reminder-toolbar">
      <label class="ci-field"
        >稍后提醒的间隔<select v-model.number="snoozeMinutes" class="ci-input">
          <option :value="15">15 分钟</option>
          <option :value="30">30 分钟</option>
          <option :value="60">1 小时</option>
          <option :value="120">2 小时</option>
        </select></label
      ><RouterLink class="ci-btn ci-text cursor-interaction" to="/settings">提醒设置 ↗</RouterLink>
    </div>
    <p class="ci-notice ci-before">
      即将截止与逾期分别提醒；即将截止提醒即使已读或延后，到期后仍会出现新的逾期提醒。
    </p>
    <p v-if="!store.reminderFeed && store.reminderLoading" class="ci-loading" role="status">
      正在检查最新截止时间…
    </p>
    <div v-else-if="store.reminderFeed && !items.length" class="ci-first">
      <span class="ci-firstemoji">🌿</span>
      <h2>{{ tab === 'unread' ? '当前没有未读提醒' : '这里暂时没有提醒' }}</h2>
      <p>进入提前提醒范围的任务会出现在这里；没有明确截止时间的任务暂不提醒。</p>
      <RouterLink class="ci-btn cursor-interaction" to="/tasks">看看任务清单 →</RouterLink>
    </div>
    <div class="ci-reminder-list">
      <article
        v-for="item in items"
        :key="item.id"
        class="ci-reminder-card"
        :class="{ 'ci-reminder-overdue': item.phase === 'overdue' }"
        :aria-label="`${item.task.title}的提醒`"
      >
        <div class="ci-reminder-heading">
          <span class="ci-reminder-phase">{{
            item.phase === 'overdue' ? '⏰ 已逾期' : '🔔 即将截止'
          }}</span
          ><span class="ci-tag">{{
            item.status === 'read' ? '已读' : item.status === 'snoozed' ? '稍后提醒' : '未读'
          }}</span>
        </div>
        <h2>
          <RouterLink :to="`/tasks/${item.task.id}`">{{ item.task.title }}</RouterLink>
        </h2>
        <p>{{ item.task.course || '待分类' }} · 截止 {{ displayDeadline(item, true) }}</p>
        <p v-if="item.snoozed_until" class="ci-notice ci-before">
          {{ displayDate(item.snoozed_until, true) }} 后重新提醒。
        </p>
        <div class="ci-actions">
          <button
            v-if="item.status !== 'read'"
            class="ci-btn ci-primary cursor-interaction"
            :disabled="!!store.reminderBusy"
            @click="store.actOnReminder(item, 'read')"
          >
            标为已读 ✓
          </button>
          <button
            v-if="item.status === 'unread'"
            class="ci-btn cursor-interaction"
            :disabled="!!store.reminderBusy"
            @click="store.actOnReminder(item, 'snooze', snoozeMinutes)"
          >
            稍后提醒 💤
          </button>
          <button
            v-if="item.status !== 'unread'"
            class="ci-btn cursor-interaction"
            :disabled="!!store.reminderBusy"
            @click="store.actOnReminder(item, 'restore')"
          >
            恢复未读 ↶
          </button>
          <RouterLink class="ci-btn ci-text cursor-interaction" :to="`/tasks/${item.task.id}`"
            >查看任务 →</RouterLink
          >
        </div>
      </article>
    </div>
  </template>
  <p v-if="store.reminderFeed" class="ci-evidence ci-reminder-updated">
    上次检查：{{ displayDate(store.reminderFeed.evaluated_at, true) }} · 稍后和已读状态保存在本机。
  </p>
</template>
