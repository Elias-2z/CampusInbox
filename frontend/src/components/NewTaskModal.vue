<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  emptyDraft,
  localInput,
  post,
  request,
  serialize,
  type FileImport,
  type ImportCapabilities,
  type InboxItem,
  type Task,
} from '@/api'
import { useCampus } from '@/stores/campus'
import AppModal from './AppModal.vue'
import TaskForm from './TaskForm.vue'
import { canExtractLocally } from '@/localOcr'

type Mode = 'text' | 'image' | 'file' | 'manual'
const modes: { id: Mode; icon: string; title: string; hint: string }[] = [
  { id: 'text', icon: '📝', title: '文本粘贴', hint: '一条通知，直接贴进来' },
  { id: 'image', icon: '🖼️', title: '图片上传', hint: '截图或拍照，先识别文字' },
  { id: 'file', icon: '📎', title: '文件上传', hint: '课程文档，提取后再核对' },
  { id: 'manual', icon: '✍️', title: '手动填写', hint: '信息清楚，自己记下来' },
]
const store = useCampus()
const router = useRouter()
const mode = ref<Mode>('text')
const text = ref('')
const draft = ref(emptyDraft())
const chatSource = store.chatImportSource
const source = ref(chatSource || '手动粘贴')
const sentAt = ref(chatSource ? '' : localInput(new Date().toISOString()))
const unknownTime = ref(false)
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai'
const capabilities = ref<ImportCapabilities | null>(null)
const saved = ref<FileImport[]>([])
const attachment = ref<FileImport | null>(null)
const file = ref<File | null>(null)
const previewUrl = ref('')
const consent = ref(false)
const extractor = ref<'local' | 'cloud'>('cloud')
const uploading = ref(false)
const polling = ref(false)
const error = ref('')
const loadingSaved = ref(false)
const fileInput = ref<HTMLInputElement>()
let timer: number | undefined
let disposed = false
let selection = 0
let pollCount = 0
const isUpload = computed(() => mode.value === 'image' || mode.value === 'file')
const isLocal = computed(() => /\.(txt|md)$/i.test(file.value?.name || ''))
const localOcrAvailable = computed(
  () =>
    !!file.value &&
    canExtractLocally(
      file.value.name,
      file.value.size,
      !!capabilities.value?.local_ocr,
      !!capabilities.value?.local_docx,
    ),
)
const localRetryAvailable = computed(
  () =>
    !!attachment.value &&
    canExtractLocally(
      attachment.value.filename,
      attachment.value.size,
      !!capabilities.value?.local_ocr,
      !!capabilities.value?.local_docx,
    ),
)
const cloudSupported = computed(
  () => !/\.bmp$/i.test(file.value?.name || attachment.value?.filename || ''),
)
const uploadAllowed = computed(
  () =>
    isLocal.value ||
    (extractor.value === 'local'
      ? localOcrAvailable.value
      : cloudSupported.value && !!consent.value && !!capabilities.value?.mineru_configured),
)
const locked = computed(() => store.busy || uploading.value)
const submitWithoutTime = computed(() => !!chatSource && mode.value === 'text' && unknownTime.value)
const canSubmit = computed(
  () =>
    text.value.trim().length >= 2 &&
    text.value.length <= 20000 &&
    !!source.value.trim() &&
    (submitWithoutTime.value || Number.isFinite(new Date(sentAt.value).getTime())) &&
    (!isUpload.value || attachment.value?.status === 'ready'),
)
const accept = computed(() =>
  mode.value === 'image' ? '.png,.jpg,.jpeg,.webp,.bmp' : '.txt,.md,.pdf,.doc,.docx,.ppt,.pptx',
)

async function loadSaved() {
  loadingSaved.value = true
  try {
    const [config, files] = await Promise.all([
      request<ImportCapabilities>('/imports/capabilities'),
      request<FileImport[]>('/imports/files'),
    ])
    capabilities.value = config
    saved.value = files
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    loadingSaved.value = false
  }
}
onMounted(loadSaved)
onUnmounted(() => {
  disposed = true
  window.clearTimeout(timer)
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
})
function clearFile() {
  selection++
  window.clearTimeout(timer)
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
  file.value = null
  attachment.value = null
  consent.value = false
  extractor.value = 'cloud'
  polling.value = false
  if (fileInput.value) fileInput.value.value = ''
}
function chooseMode(next: Mode) {
  if (locked.value || next === mode.value) return
  if (isUpload.value) {
    clearFile()
    text.value = ''
  }
  mode.value = next
  error.value = ''
  store.error = ''
  source.value =
    next === 'text' ? chatSource || '手动粘贴' : next === 'image' ? '图片上传' : '文件上传'
}
function chooseFile(event: Event) {
  const chosen = (event.target as HTMLInputElement).files?.[0]
  clearFile()
  text.value = ''
  error.value = ''
  if (!chosen) return
  const extensions = accept.value.split(',')
  const extension = `.${chosen.name.split('.').at(-1)?.toLowerCase()}`
  if (!extensions.includes(extension)) {
    error.value = '请选择此方式支持的文件类型。'
    return
  }
  if (chosen.size === 0 || chosen.size > 20 * 1024 * 1024) {
    error.value = '请选择非空且不超过 20 MB 的文件。'
    return
  }
  file.value = chosen
  if (
    canExtractLocally(
      chosen.name,
      chosen.size,
      !!capabilities.value?.local_ocr,
      !!capabilities.value?.local_docx,
    )
  )
    extractor.value = 'local'
  if (mode.value === 'image') previewUrl.value = URL.createObjectURL(chosen)
}
function applyResult(result: FileImport) {
  attachment.value = result
  if (result.status === 'ready') text.value = result.text
  error.value = result.error
}
async function poll() {
  window.clearTimeout(timer)
  const current = attachment.value
  if (!current || current.status !== 'parsing' || polling.value) return
  const revision = selection
  polling.value = true
  try {
    const result = await post<FileImport>(`/imports/files/${current.id}/poll`)
    if (disposed || revision !== selection) return
    applyResult(result)
    pollCount++
    if (result.status === 'parsing' && !result.error && pollCount < 150)
      timer = window.setTimeout(() => void poll(), 4000)
  } catch (e) {
    if (!disposed && revision === selection) error.value = store.errorMessage(e)
  } finally {
    if (revision === selection) polling.value = false
  }
}
function continuePolling() {
  pollCount = 0
  void poll()
}
async function upload() {
  if (!file.value || uploading.value) return
  if (!uploadAllowed.value) return
  uploading.value = true
  error.value = ''
  const revision = selection
  try {
    const query = new URLSearchParams({
      filename: file.value.name,
      consent: String(consent.value),
      extractor: extractor.value,
    })
    const result = await request<FileImport>(`/imports/files?${query}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: file.value,
    })
    if (disposed || revision !== selection) return
    applyResult(result)
    pollCount = 0
    if (result.status === 'parsing') void poll()
    void loadSaved()
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    uploading.value = false
  }
}
function resume(item: FileImport) {
  clearFile()
  text.value = ''
  applyResult(item)
  source.value = `附件：${item.filename}`.slice(0, 100)
  pollCount = 0
  if (item.status === 'parsing') void poll()
}
async function retry(method: 'cloud' | 'local' = 'cloud') {
  if (!attachment.value) return
  uploading.value = true
  try {
    applyResult(
      await post<FileImport>(
        `/imports/files/${attachment.value.id}/${method === 'local' ? 'local-ocr' : `retry?consent=${consent.value}`}`,
      ),
    )
    pollCount = 0
    void poll()
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    uploading.value = false
  }
}
async function submit() {
  if (mode.value === 'manual') {
    const task = await store.mutate(
      () => post<Task>('/tasks', serialize(draft.value)),
      '新任务已加入清单。',
    )
    if (task) {
      store.importing = false
      await router.push(`/tasks/${task.id}`)
    }
    return
  }
  if (!canSubmit.value) return
  const result = await store.mutate(
    () =>
      post<InboxItem>('/inbox', {
        text: text.value.trim(),
        source: source.value.trim(),
        sent_at: submitWithoutTime.value ? null : new Date(sentAt.value).toISOString(),
        timezone,
        attachment_id: isUpload.value ? attachment.value?.id : undefined,
      }),
    '内容已整理，请核对字段后确认。',
  )
  if (result) {
    store.importing = false
    await router.push({ path: '/inbox', query: { id: result.id } })
  }
}
</script>

<template>
  <AppModal title="✨ 新建任务" :locked="locked" @close="store.importing = false">
    <p class="ci-notice ci-before">从一条通知开始，或者直接记下一件事。</p>
    <div class="ci-create-modes" role="group" aria-label="新建方式">
      <button
        v-for="option in modes"
        :key="option.id"
        type="button"
        class="ci-create-mode"
        :aria-pressed="mode === option.id"
        :disabled="locked"
        @click="chooseMode(option.id)"
      >
        <span aria-hidden="true">{{ option.icon }}</span
        ><b>{{ option.title }}</b>
        <small>{{ option.hint }}</small>
      </button>
    </div>
    <form @submit.prevent="submit">
      <TaskForm v-if="mode === 'manual'" v-model="draft" :busy="locked" />
      <template v-else>
        <template v-if="isUpload">
          <div class="ci-uploadbox">
            <label class="ci-field"
              >{{ mode === 'image' ? '选择通知截图或照片' : '选择课程文件' }}
              <input
                ref="fileInput"
                type="file"
                class="ci-input"
                :accept="accept"
                :disabled="locked"
                @change="chooseFile"
              />
            </label>
            <p class="ci-notice">
              {{ mode === 'image' ? 'PNG、JPG、WebP、BMP' : 'TXT、Markdown、PDF、Word、PPT' }} ·
              云端上限 20 MB，本机解析上限 8 MB
            </p>
            <img
              v-if="previewUrl"
              :src="previewUrl"
              alt="待识别图片预览"
              class="ci-uploadpreview"
            />
            <p v-if="file" class="ci-filename">
              📎 {{ file.name }} · {{ (file.size / 1024).toFixed(1) }} KB
            </p>
            <template v-if="file && !attachment">
              <p v-if="isLocal" class="ci-notice">文本文件直接在本机读取，不发送到 MinerU。</p>
              <template v-else>
                <div v-if="localOcrAvailable" class="ci-actions">
                  <label
                    ><input v-model="extractor" type="radio" value="local" :disabled="locked" />
                    本机解析</label
                  >
                  <label v-if="!/\.bmp$/i.test(file.name)"
                    ><input v-model="extractor" type="radio" value="cloud" :disabled="locked" />
                    MinerU 云端解析</label
                  >
                </div>
                <p v-if="extractor === 'local'" class="ci-notice">
                  文件在本机解析，不发送到云端。请对照原文件核对提取的文字。
                </p>
                <p v-if="!localOcrAvailable && !cloudSupported" class="ci-inlineerror">
                  BMP 需要可用的 Windows 本机 OCR，文件不超过 8 MB。
                </p>
                <p
                  v-if="extractor === 'cloud' && !capabilities?.mineru_configured"
                  class="ci-inlineerror"
                  role="status"
                >
                  尚未配置 MinerU Token。配置后重新打开此窗口；也可以先粘贴文本或手动填写。
                </p>
                <label v-if="extractor === 'cloud'" class="ci-checklabel"
                  ><input v-model="consent" type="checkbox" :disabled="locked" />
                  我同意将此文件发送到 MinerU 云端解析，可能消耗账户额度。</label
                >
              </template>
              <button
                type="button"
                class="ci-btn ci-primary"
                :disabled="locked || !uploadAllowed"
                @click="upload"
              >
                {{
                  uploading
                    ? '正在提取…'
                    : isLocal
                      ? '提取文本 →'
                      : extractor === 'local'
                        ? '本机识别 →'
                        : '发送到 MinerU 并解析 →'
                }}
              </button>
            </template>
          </div>
          <section v-if="attachment" class="ci-file-result" aria-live="polite">
            <b>{{
              attachment.status === 'ready'
                ? '✅ 文本已提取，请校正后继续'
                : attachment.status === 'failed'
                  ? '解析未完成'
                  : '⏳ 正在解析…'
            }}</b>
            <a class="ci-attachmentlink" :href="`/api/attachments/${attachment.id}/download`"
              >📎 {{ attachment.filename }} · 下载原文件</a
            >
            <p v-if="attachment.status === 'parsing'" class="ci-notice">
              可以关闭窗口，稍后从“最近上传”继续。不会自动创建任务。
            </p>
            <button
              v-if="attachment.status === 'parsing'"
              type="button"
              class="ci-btn"
              :disabled="polling || locked"
              @click="continuePolling"
            >
              {{ polling ? '正在查询…' : '继续查询进度' }}
            </button>
            <label
              v-if="
                (attachment.status === 'failed' || (attachment.status === 'parsing' && error)) &&
                cloudSupported &&
                capabilities?.mineru_configured
              "
              class="ci-checklabel"
              ><input v-model="consent" type="checkbox" :disabled="locked" />我同意将文件发送到
              MinerU 云端解析。</label
            >
            <button
              v-if="attachment.status === 'failed' || (attachment.status === 'parsing' && error)"
              type="button"
              class="ci-btn"
              :disabled="locked || !consent || !cloudSupported || !capabilities?.mineru_configured"
              @click="retry('cloud')"
            >
              使用 MinerU 重新解析
            </button>
            <button
              v-if="attachment.status === 'failed' && localRetryAvailable"
              type="button"
              class="ci-btn"
              :disabled="locked"
              @click="retry('local')"
            >
              用本机解析重试
            </button>
          </section>
          <details v-if="saved.length" class="ci-recentfiles">
            <summary>最近上传 · 继续处理</summary>
            <button
              v-for="item in saved"
              :key="item.id"
              type="button"
              class="ci-recentfile"
              :disabled="locked"
              @click="resume(item)"
            >
              <span>📎 {{ item.filename }}</span
              ><small>{{
                item.status === 'ready'
                  ? '待校正'
                  : item.status === 'failed'
                    ? '上传失败'
                    : '解析中'
              }}</small>
            </button>
          </details>
          <p v-if="loadingSaved" class="ci-notice">正在读取上传记录…</p>
        </template>
        <template v-if="!isUpload || attachment?.status === 'ready'">
          <label class="ci-field"
            >{{ isUpload ? '提取文本（可校正）' : '通知原文' }}
            <textarea
              v-model="text"
              class="ci-input ci-noticeinput"
              required
              minlength="2"
              :maxlength="isUpload ? undefined : 20000"
              placeholder="粘贴老师或课程群的通知……"
              :disabled="locked"
            />
            <small class="ci-evidence"
              >{{ text.length }} / 20000 字 ·
              核对日期、编号和提交要求；较长文档请保留本条通知相关内容。</small
            >
          </label>
          <button
            v-if="mode === 'text'"
            type="button"
            class="ci-btn ci-text"
            :disabled="locked"
            @click="
              text = '软件工程：请于明天18:00前提交需求分析报告，提交至学习通，文件名为学号+姓名。'
            "
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
                :disabled="locked"
            /></label>
            <label class="ci-field"
              >消息实际发送时间<input
                v-model="sentAt"
                type="datetime-local"
                :required="!submitWithoutTime"
                class="ci-input"
                :disabled="locked || submitWithoutTime"
            /></label>
          </div>
          <label v-if="chatSource && mode === 'text'" class="ci-checklabel">
            <input v-model="unknownTime" type="checkbox" :disabled="locked" />
            不清楚发送时间
          </label>
          <p class="ci-notice ci-before">
            <template v-if="chatSource && mode === 'text'">
              可在来源名称补充群名和发件人，例如“{{ chatSource }} · 软件工程群 · 任课教师”。
            </template>
            <template v-if="submitWithoutTime">请在审核时对照原文手动填写截止时间。</template>
            <template v-else>以消息发送时间解释“明天 / 下周”。当前时区：{{ timezone }}。</template>
          </p>
        </template>
      </template>
      <div v-if="error || store.error" class="ci-inlineerror" role="alert">
        {{ error || store.error }}
      </div>
      <div
        v-if="mode === 'manual' || !isUpload || attachment?.status === 'ready'"
        class="ci-actions"
      >
        <button class="ci-btn ci-primary" :disabled="locked || (mode !== 'manual' && !canSubmit)">
          {{
            store.busy
              ? '正在整理，请稍候…'
              : mode === 'manual'
                ? '创建任务 ✓'
                : '识别并前往收件箱审核 →'
          }}
        </button>
      </div>
    </form>
  </AppModal>
</template>
