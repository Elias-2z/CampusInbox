<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { localInput, post, type InboxItem } from '@/api'
import { useCampus } from '@/stores/campus'
import AppModal from './AppModal.vue'
const store = useCampus()
const router = useRouter()
const text = ref('')
const source = ref('手动粘贴')
const sentAt = ref(localInput(new Date().toISOString()))
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai'
const sample = '软件工程：请于明天18:00前提交需求分析报告，提交至学习通，文件名为学号+姓名。'
async function submit() {
  if (text.value.trim().length < 2) return
  const known = new Set(store.inbox.map((i) => i.id))
  const result = await store.mutate(
    () =>
      post<InboxItem>('/inbox', {
        text: text.value.trim(),
        source: source.value.trim(),
        sent_at: new Date(sentAt.value).toISOString(),
        timezone,
      }),
    '通知已整理，请核对字段后确认。',
  )
  if (result) {
    if (known.has(result.id)) store.notice = '这条通知已经导入，已打开原有记录。'
    store.importing = false
    await router.push({ path: '/inbox', query: { id: result.id } })
  }
}
</script>
<template>
  <AppModal title="📮 给通知找个归宿" :locked="store.busy" @close="store.importing = false">
    <form @submit.prevent="submit">
      <p class="ci-notice ci-before">一次导入一条任务通知。先保留原文，再由你确认是否创建任务。</p>
      <label class="ci-field"
        >通知原文<textarea
          v-model="text"
          class="ci-input ci-noticeinput"
          autofocus
          required
          minlength="2"
          maxlength="20000"
          placeholder="粘贴老师或课程群的通知……"
          :disabled="store.busy"
        />
      </label>
      <button
        type="button"
        class="ci-btn ci-text cursor-interaction"
        :disabled="store.busy"
        @click="text = sample"
      >
        试填一条示例通知 ↗
      </button>
      <div class="ci-fieldrow">
        <label class="ci-field"
          >来源名称<input
            v-model="source"
            required
            maxlength="100"
            class="ci-input"
            :disabled="store.busy"
        /></label>
        <label class="ci-field"
          >消息实际发送时间<input
            v-model="sentAt"
            type="datetime-local"
            required
            class="ci-input"
            :disabled="store.busy"
        /></label>
      </div>
      <p class="ci-notice ci-before">以发送时间解释“明天 / 下周”。当前时区：{{ timezone }}。</p>
      <div v-if="store.error" class="ci-inlineerror" role="alert">{{ store.error }}</div>
      <div class="ci-actions">
        <button class="ci-btn ci-primary cursor-interaction" :disabled="store.busy || !text.trim()">
          {{ store.busy ? '正在整理，请稍候…' : '整理为待审核建议 →' }}
        </button>
      </div>
    </form>
  </AppModal>
</template>
