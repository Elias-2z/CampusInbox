<script setup lang="ts">
// Interaction adapted from Elias-2z/CampusInbox 1d02252; uses this project's authenticated API.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { download, post, request, refresh, type FileImport, type ImportCapabilities } from '../api'
import type { Task } from '../domain'
const emit = defineEmits<{ close: []; review: []; created: [task: Task] }>()
const modal = ref<HTMLDialogElement | null>(null)
const busy = ref(false)
const errorMessage = (value: unknown) =>
  value instanceof Error ? value.message : '操作失败，请稍后重试。'
type Mode = 'text' | 'image' | 'file' | 'manual'
const modes: { id: Mode; icon: string; title: string; hint: string }[] = [
  { id: 'text', icon: '📝', title: '文本粘贴', hint: '一条通知，直接贴进来' },
  { id: 'image', icon: '🖼️', title: '图片上传', hint: '截图或拍照，先识别文字' },
  { id: 'file', icon: '📎', title: '文件上传', hint: '课程文档，提取后再核对' },
  { id: 'manual', icon: '✍️', title: '手动填写', hint: '信息清楚，自己记下来' },
]
const mode = ref<Mode>('text')
const text = ref('')
const draft = ref({
  title: '',
  course: '',
  scope: '',
  kind: 'assignment',
  deadline: '',
  requirements: '',
  submission_method: '',
  teacher: '',
})
const deadlineMode = ref('none')
const requestId = Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) =>
  byte.toString(16).padStart(2, '0'),
).join('')
const source = ref('手动粘贴')
const sentAt = ref(new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 16))
const timezone = 'Asia/Shanghai'
const capabilities = ref<ImportCapabilities | null>(null)
const saved = ref<FileImport[]>([])
const attachment = ref<FileImport | null>(null)
const file = ref<File | null>(null)
const previewUrl = ref('')
const consent = ref(false)
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
const locked = computed(() => busy.value || uploading.value)
const canSubmit = computed(
  () =>
    text.value.trim().length >= 2 &&
    text.value.length <= 20000 &&
    (!isUpload.value || attachment.value?.status === 'ready'),
)
const accept = computed(() =>
  mode.value === 'image' ? '.png,.jpg,.jpeg,.webp' : '.txt,.md,.pdf,.doc,.docx,.ppt,.pptx',
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
    error.value = errorMessage(e)
  } finally {
    loadingSaved.value = false
  }
}
onMounted(() => {
  modal.value?.showModal()
  void loadSaved()
})
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
  polling.value = false
  if (fileInput.value) fileInput.value.value = ''
}
function close() {
  if (!locked.value) emit('close')
}
function chooseMode(next: Mode) {
  if (locked.value || next === mode.value) return
  if (isUpload.value) {
    clearFile()
    text.value = ''
  }
  mode.value = next
  error.value = ''
  error.value = ''
  source.value = next === 'text' ? '手动粘贴' : next === 'image' ? '图片上传' : '文件上传'
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
    if (!disposed && revision === selection) error.value = errorMessage(e)
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
  if (!isLocal.value && (!consent.value || !capabilities.value?.mineru_configured)) return
  uploading.value = true
  error.value = ''
  const revision = selection
  try {
    const query = new URLSearchParams({ filename: file.value.name, consent: String(consent.value) })
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
    error.value = errorMessage(e)
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
async function retry() {
  if (!attachment.value) return
  const revision = selection
  uploading.value = true
  try {
    const result = await post<FileImport>(`/imports/files/${attachment.value.id}/retry`)
    if (disposed || revision !== selection) return
    applyResult(result)
    pollCount = 0
    void poll()
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    uploading.value = false
  }
}
async function downloadOriginal() {
  if (!attachment.value) return
  try {
    await download('/attachments/' + attachment.value.id + '/download', attachment.value.filename)
  } catch (e) {
    error.value = errorMessage(e)
  }
}
async function submit() {
  if (locked.value || (mode.value !== 'manual' && !canSubmit.value)) return
  busy.value = true
  error.value = ''
  try {
    if (mode.value === 'manual') {
      const task = await post<Task>('/tasks', {
        ...draft.value,
        request_id: requestId,
        deadline:
          deadlineMode.value === 'none' || !draft.value.deadline
            ? null
            : deadlineMode.value === 'date'
              ? draft.value.deadline
              : draft.value.deadline + ':00+08:00',
        requirements: draft.value.requirements
          .split('\n')
          .map((value) => value.trim())
          .filter(Boolean),
      })
      await refresh()
      emit('created', task)
    } else {
      await post('/imports/submit', {
        text: text.value.trim(),
        source: source.value.trim(),
        published_at: sentAt.value ? sentAt.value + ':00+08:00' : undefined,
        attachment_id: isUpload.value ? attachment.value?.id : undefined,
        mode: 'default',
      })
      await refresh()
      emit('review')
    }
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <dialog
    ref="modal"
    class="ci-modal ci-newtask"
    aria-labelledby="new-task-title"
    @cancel.prevent="close"
  >
    <header class="ci-newtask-heading">
      <h2 id="new-task-title">✨ 新建任务</h2>
      <button
        type="button"
        class="ci-btn ci-newtask-close"
        aria-label="关闭新建任务"
        :disabled="locked"
        @click="close"
      >
        ×
      </button>
    </header>
    <div class="ci-modalbody ci-newtask-body">
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
        <div v-if="mode === 'manual'" class="ci-manual-form">
          <label class="ci-field"
            >任务名称<input
              v-model="draft.title"
              class="ci-input"
              required
              maxlength="160"
              :disabled="locked"
          /></label>
          <div class="ci-fieldrow">
            <label class="ci-field"
              >课程<input
                v-model="draft.course"
                class="ci-input"
                maxlength="100"
                :disabled="locked"
            /></label>
            <label class="ci-field"
              >班级 / 分组<input
                v-model="draft.scope"
                class="ci-input"
                maxlength="100"
                :disabled="locked"
            /></label>
          </div>
          <div class="ci-fieldrow">
            <label class="ci-field"
              >任务类型<select v-model="draft.kind" class="ci-input" :disabled="locked">
                <option value="assignment">作业</option>
                <option value="experiment">实验</option>
                <option value="exam">考试</option>
                <option value="project">项目</option>
                <option value="presentation">展示</option>
                <option value="other">其他</option>
              </select></label
            >
            <label class="ci-field"
              >截止精度<select
                v-model="deadlineMode"
                class="ci-input"
                :disabled="locked"
                @change="draft.deadline = ''"
              >
                <option value="none">未明确</option>
                <option value="date">仅日期</option>
                <option value="minute">日期与时间（北京时间）</option>
              </select></label
            >
          </div>
          <label v-if="deadlineMode !== 'none'" class="ci-field"
            >{{ deadlineMode === 'date' ? '截止日期' : '截止时间（北京时间）'
            }}<input
              v-model="draft.deadline"
              class="ci-input"
              :type="deadlineMode === 'date' ? 'date' : 'datetime-local'"
              required
              :disabled="locked"
          /></label>
          <label class="ci-field"
            >任务要求（每行一项）<textarea
              v-model="draft.requirements"
              class="ci-input"
              rows="4"
              :disabled="locked"
            />
          </label>
          <div class="ci-fieldrow">
            <label class="ci-field"
              >提交方式<input
                v-model="draft.submission_method"
                class="ci-input"
                maxlength="160"
                :disabled="locked" /></label
            ><label class="ci-field"
              >教师<input
                v-model="draft.teacher"
                class="ci-input"
                maxlength="100"
                :disabled="locked"
            /></label>
          </div>
          <p class="ci-notice">
            点击创建后直接加入正式任务，无需再次审核。本地记录不会代替你在原平台提交。
          </p>
        </div>
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
                {{ mode === 'image' ? 'PNG、JPG、WebP' : 'TXT、Markdown、PDF、Word、PPT' }} ·
                单个文件不超过 20 MB
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
                  <p v-if="!capabilities?.mineru_configured" class="ci-inlineerror" role="status">
                    尚未配置 MinerU Token。配置后重新打开此窗口；也可以先粘贴文本或手动填写。
                  </p>
                  <label class="ci-checklabel"
                    ><input v-model="consent" type="checkbox" :disabled="locked" />
                    我同意将此文件发送到 MinerU 云端解析，可能消耗账户额度。</label
                  >
                </template>
                <button
                  type="button"
                  class="ci-btn ci-primary"
                  :disabled="locked || (!isLocal && (!consent || !capabilities?.mineru_configured))"
                  @click="upload"
                >
                  {{ uploading ? '正在上传…' : isLocal ? '提取文本 →' : '发送到 MinerU 并解析 →' }}
                </button>
              </template>
            </div>
            <section v-if="attachment" class="ci-file-result" aria-live="polite">
              <b>{{
                attachment.status === 'ready'
                  ? '✅ 文本已提取，请校正后继续'
                  : attachment.status === 'failed'
                    ? '解析未完成'
                    : '⏳ MinerU 正在解析…'
              }}</b>
              <button type="button" class="ci-btn ci-attachmentlink" @click="downloadOriginal">
                📎 {{ attachment.filename }} · 下载原文件
              </button>
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
              <button
                v-if="attachment.status === 'failed' || (attachment.status === 'parsing' && error)"
                type="button"
                class="ci-btn"
                :disabled="locked"
                @click="retry"
              >
                重新解析
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
                :aria-label="isUpload ? '提取文本（可校正）' : '通知原文'"
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
                text =
                  '软件工程：请于明天18:00前提交需求分析报告，提交至学习通，文件名为学号+姓名。'
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
                  required
                  class="ci-input"
                  :disabled="locked"
              /></label>
            </div>
            <p class="ci-notice ci-before">
              以消息发送时间解释“明天 / 下周”。当前时区：{{ timezone }}。
            </p>
          </template>
        </template>
        <div v-if="error" class="ci-inlineerror" role="alert">
          {{ error }}
        </div>
        <div
          v-if="mode === 'manual' || !isUpload || attachment?.status === 'ready'"
          class="ci-actions"
        >
          <button class="ci-btn ci-primary" :disabled="locked || (mode !== 'manual' && !canSubmit)">
            {{
              busy
                ? '正在整理，请稍候…'
                : mode === 'manual'
                  ? '创建任务 ✓'
                  : '识别并前往收件箱审核 →'
            }}
          </button>
        </div>
      </form>
    </div>
  </dialog>
</template>
