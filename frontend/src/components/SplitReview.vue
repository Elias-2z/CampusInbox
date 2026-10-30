<script setup lang="ts">
import { computed } from 'vue'
import { post, type InboxItem } from '@/api'
import { useCampus } from '@/stores/campus'
import SplitTaskDraft from '@/components/SplitTaskDraft.vue'
const props = defineProps<{ item: InboxItem }>()
const store = useCampus()
const confirmed = computed(
  () => props.item.split_items.filter((d) => d.status === 'accepted').length,
)
const remaining = computed(
  () => props.item.split_items.filter((d) => d.status === 'pending').length,
)
async function generate() {
  await store.mutate(
    () => post<InboxItem>(`/suggestions/${props.item.id}/split`),
    '草稿已拆开，请逐项审核后确认。',
  )
}
</script>
<template>
  <section aria-label="多任务拆分审核">
    <h3>🧩 一条通知，分开安排</h3>
    <p class="ci-notice ci-before">按编号拆开后，每项单独核对并确认。所有任务共享左侧完整原文。</p>
    <template v-if="!item.split_items.length">
      <p class="ci-notice ci-before">
        支持从 1 开始连续编号、每项另起一行的 2–20
        个条目。草稿使用规则提取，含糊的时间留空；公共说明请对照原文核对。
      </p>
      <button
        v-if="item.status === 'pending'"
        class="ci-btn ci-primary cursor-interaction"
        :disabled="store.busy"
        @click="generate"
      >
        {{ store.busy ? '正在整理…' : '生成拆分草稿 →' }}
      </button>
      <button
        v-else-if="item.status === 'ignored'"
        class="ci-btn cursor-interaction"
        :disabled="store.busy"
        @click="store.mutate(() => post(`/suggestions/${item.id}/reopen`), '已恢复待处理。')"
      >
        恢复为待处理
      </button>
    </template>
    <template v-else>
      <p class="ci-split-progress" role="status">
        ✨ 已创建 {{ confirmed }} 项 · 待审核 {{ remaining }} 项 · 共
        {{ item.split_items.length }} 项
      </p>
      <p v-if="!remaining" class="ci-notice ci-before">本条通知已处理。跳过的条目仍可恢复审核。</p>
      <SplitTaskDraft v-for="draft in item.split_items" :key="draft.id" :item="draft" />
    </template>
  </section>
</template>
