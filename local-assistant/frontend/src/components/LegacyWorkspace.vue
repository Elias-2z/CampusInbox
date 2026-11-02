<script setup lang="ts">
import { ref } from 'vue'
const props = defineProps<{ view: string; panel?: string; taskId?: string; messageId?: string }>()
const frame = ref<HTMLIFrameElement | null>(null)
const query = new URLSearchParams({ embedded: '1' })
if (props.panel) query.set('panel', props.panel)
if (props.taskId) query.set('task', props.taskId)
if (props.messageId) query.set('message', props.messageId)
const src = `/legacy.html?${query}#${props.view}`
function isSource(source: MessageEventSource | null) {
  return source === frame.value?.contentWindow
}
function hasUnsaved() {
  return !!frame.value?.contentDocument?.querySelector('.proposal-card[data-dirty="true"]')
}
function refresh() {
  frame.value?.contentWindow?.postMessage({ type: 'campusinbox:refresh' }, location.origin)
}
function revealDialog() {
  frame.value?.scrollIntoView({ block: 'start' })
}
defineExpose({ isSource, hasUnsaved, refresh, revealDialog })
</script>
<template>
  <iframe
    ref="frame"
    class="ci-workspace-frame"
    :class="{ 'ci-panel-frame': panel }"
    :src="src"
    :title="panel ? '完整任务操作' : '业务工作区：' + view"
  />
</template>
