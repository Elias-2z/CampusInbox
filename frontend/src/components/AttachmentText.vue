<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { post, request, type AttachmentInfo, type FileImport, type ImportCapabilities } from '@/api'
import { canExtractLocally } from '@/localOcr'

const props = defineProps<{ attachment: AttachmentInfo }>()
const text = ref('')
const error = ref('')
const parseError = ref('')
const details = ref<HTMLDetailsElement | null>(null)
const loaded = ref(false)
const loading = ref(false)
const working = ref(false)
const localAvailable = ref(false)
const localResult = ref<FileImport | null>(null)
let textRevision = 0
const status = computed(() => localResult.value?.status ?? props.attachment.status)
watch(
  () => props.attachment.status,
  async (next, previous, onCleanup) => {
    if (previous !== undefined) {
      textRevision++
      localResult.value = null
      loaded.value = false
      loading.value = false
      error.value = ''
      parseError.value = ''
      if (details.value?.open && next !== 'parsing') void load()
    }
    if (next !== 'failed') return
    let active = true
    onCleanup(() => (active = false))
    try {
      const capabilities = await request<ImportCapabilities>('/imports/capabilities')
      if (!active) return
      localAvailable.value = canExtractLocally(
        props.attachment.filename,
        props.attachment.size,
        capabilities.local_ocr,
        capabilities.local_docx,
      )
    } catch {
      // Downloading the original remains available if the capability check fails.
    }
  },
  { immediate: true },
)
async function recognize() {
  if (working.value) return
  textRevision++
  loading.value = false
  working.value = true
  error.value = ''
  parseError.value = ''
  try {
    const result = await post<FileImport>(`/imports/files/${props.attachment.id}/local-ocr`)
    localResult.value = result
    text.value = result.text
    loaded.value = true
    if (result.status === 'ready') {
      error.value = result.error
    } else {
      parseError.value = result.error || '本机识别未完成，请下载原文件核对。'
    }
  } catch (cause) {
    parseError.value =
      cause instanceof Error ? cause.message : '本机识别未完成，请重试或下载原文件。'
  } finally {
    working.value = false
  }
}
async function load() {
  if (!details.value?.open || loaded.value || loading.value || working.value) return
  const revision = textRevision
  loading.value = true
  error.value = ''
  try {
    const result = await request<FileImport>(`/attachments/${props.attachment.id}/text`)
    if (revision !== textRevision) return
    text.value = result.text
    error.value = result.error
    loaded.value = true
  } catch {
    if (revision === textRevision) error.value = '无法读取附件文字，请重试或下载原文件。'
  } finally {
    if (revision === textRevision) loading.value = false
  }
}
</script>

<template>
  <div>
    <a class="ci-attachmentlink" :href="`/api/attachments/${attachment.id}/download`"
      >📎 下载原始附件：{{ attachment.filename }}</a
    >
    <details v-if="status !== 'parsing'" ref="details" @toggle="load">
      <summary class="cursor-interaction">查看附件提取结果</summary>
      <p v-if="status === 'ready'" class="ci-notice">
        请对照原文件核对；可在收件箱点“重新识别”，让符合条件的附件参与任务建议。
      </p>
      <p v-if="loading" class="ci-notice">正在读取…</p>
      <p v-if="error" class="ci-inlineerror" role="alert">{{ error }}</p>
      <p v-if="loaded" class="ci-proof">{{ text || '暂无提取文字。' }}</p>
    </details>
    <template v-if="status === 'failed'">
      <small class="ci-notice">附件文字尚未就绪，请下载原文件核对。</small>
      <button
        v-if="localAvailable"
        type="button"
        class="ci-btn ci-text"
        :disabled="working"
        @click="recognize"
      >
        {{ working ? '正在本机解析…' : '本机解析附件' }}
      </button>
    </template>
    <p v-if="parseError" class="ci-inlineerror" role="alert">{{ parseError }}</p>
    <slot v-if="status === 'ready' && loaded && text.trim() && !error && !working" name="actions" />
  </div>
</template>
