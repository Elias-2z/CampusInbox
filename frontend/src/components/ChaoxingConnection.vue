<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { createChaoxingConnection } from '@/chaoxing'
import { coverageLabel } from '@/chaoxingHealth'
import { useCampus } from '@/stores/campus'
import { post, request } from '@/api'

const store = useCampus()
const {
  status,
  events,
  busy,
  error,
  refreshError,
  qrVersion,
  start,
  stop,
  perform,
  resolve,
  retry,
  fetchOriginal,
  recheckSection,
  recheckNotice,
} = createChaoxingConnection(() => {
  void store.refresh().catch(() => {
    store.error = '消息已处理，但收件箱刷新失败，请刷新页面。'
  })
})
const times = reactive<Record<string, string>>({})
const qrError = ref(false)
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || '本机时区'
const stateNames: Record<string, string> = {
  idle: '尚未连接',
  disabled: '不采集',
  paused: '已暂停',
  connecting: '正在连接',
  waiting_scan: '等待扫码',
  connected: '已登录',
  syncing: '正在检查',
  expired: '登录已过期',
  retrying: '正在重试',
  partial: '部分内容未能读取',
  error: '连接异常',
}
const eventNames: Record<string, string> = {
  pending: '等待整理',
  done: '附件待补齐',
  needs_time: '待填写发布时间',
  needs_review: '需要核对内容',
  failed: '处理失败',
}
const stateLabel = computed(() =>
  status.value ? stateNames[status.value.state] || '状态待确认' : '正在读取连接状态',
)
const sourceLabels = { notice: '课程通知', chat: '聊天消息', task_center: '作业与考试' } as const
const sectionNames = {
  unchecked: '待检查',
  queued: '已请求重查',
  partial: '部分读取',
  checked: '栏目已检查',
  failed: '读取失败',
}
type ListedTask = { key: string; title: string; state: 'listed' | 'read' | 'queued' }
const selectedSection = ref('')
const listedTasks = ref<ListedTask[]>([])
const taskListBusy = ref(false)
const taskListError = ref('')
const browserToken = ref('')
const browserBusy = ref(false)
const browserNote = ref('')
async function browserPair(revoke = false) {
  if (browserBusy.value) return
  browserBusy.value = true
  browserNote.value = ''
  try {
    if (revoke) {
      await request('/connectors/chaoxing/browser/pair', { method: 'DELETE' })
      browserToken.value = ''
      browserNote.value = '网页组件令牌已撤销。'
    } else {
      const { token } = await post<{ token: string }>('/connectors/chaoxing/browser/pair')
      browserToken.value = token
    }
  } catch (cause) {
    browserNote.value = store.errorMessage(cause)
  } finally {
    browserBusy.value = false
  }
}
async function loadTaskList(key: string) {
  if (taskListBusy.value) return
  taskListBusy.value = true
  taskListError.value = ''
  selectedSection.value = key
  listedTasks.value = []
  try {
    const items = await request<ListedTask[]>(
      '/connectors/chaoxing/sections/tasks?key=' + encodeURIComponent(key),
    )
    if (selectedSection.value === key) listedTasks.value = items
  } catch (cause) {
    taskListError.value = store.errorMessage(cause)
  } finally {
    taskListBusy.value = false
  }
}
async function recheckTask(task: ListedTask) {
  taskListBusy.value = true
  taskListError.value = ''
  try {
    await post('/connectors/chaoxing/tasks/recheck', { key: task.key })
    task.state = 'queued'
  } catch (cause) {
    taskListError.value = store.errorMessage(cause)
  } finally {
    taskListBusy.value = false
  }
}
watch(qrVersion, () => {
  qrError.value = false
})
watch(status, (value) => {
  if (!value?.task_sections?.some((section) => section.key === selectedSection.value)) {
    selectedSection.value = ''
    listedTasks.value = []
    taskListError.value = ''
  }
})
onMounted(() => void start())
onUnmounted(stop)

function disconnect() {
  if (
    window.confirm(
      '断开将暂停采集并删除本机保存的学习通登录信息，已接收的消息和任务会保留。确定断开吗？',
    )
  ) {
    selectedSection.value = ''
    listedTasks.value = []
    void perform('disconnect')
  }
}
</script>

<template>
  <section class="ci-form ci-reminder-settings" aria-label="学习通连接">
    <h3>📚 数据源连接 · 学习通</h3>
    <p class="ci-notice ci-before">
      连接后尝试读取学习通通知和任务消息，仍可能遗漏内容，请以学习通为准。收到的消息会先进入收件箱审核，不会直接创建任务。
    </p>
    <p class="ci-notice">
      自动轮询：课程通知每轮结束后约 15 秒、作业和考试每轮结束后约 30 秒继续检查。课程通知检查近 90
      天，不采集聊天；明确过期的新任务直接归档，日期不明的通知保留核对。
    </p>
    <p class="ci-notice" role="status">当前状态：{{ stateLabel }}</p>
    <p v-if="status?.session_storage" class="ci-notice">
      登录保存：{{ status.session_storage }}。
      {{
        status.session_saved
          ? '本机已保存登录状态，重启后会尝试恢复；过期仍需扫码。'
          : '扫码成功后在本机安全保存登录状态。'
      }}
    </p>
    <p v-if="status?.session_error" class="ci-inlineerror" role="alert">
      {{ status.session_error }}
    </p>
    <div v-if="status" class="ci-before">
      <p class="ci-notice">
        待处理 {{ status.counts.pending }} · 已送入收件箱 {{ status.counts.done }} · 待填写时间
        {{ status.counts.needs_time }} · 待核对 {{ status.counts.needs_review }} · 失败
        {{ status.counts.failed }}
      </p>
      <p class="ci-notice">
        最近收到：{{
          status.last_received
            ? new Date(status.last_received * 1000).toLocaleString('zh-CN')
            : '暂无'
        }}
      </p>
      <p v-if="status.freshness === 'stale'" class="ci-inlineerror" role="alert">
        采集器超过 90 秒没有响应，请点击“扫码连接 / 恢复”。
      </p>
      <details v-if="status.sources" class="ci-detailsection">
        <summary>查看各来源检查情况</summary>
        <p class="ci-notice">
          这些信息仅保留本次服务运行期间的记录，不能保证平台全部消息都已读取。
        </p>
        <div v-for="(source, key) in status.sources" :key="key" class="ci-before">
          <b>{{ sourceLabels[key] }}</b> · {{ stateNames[source.state] || '状态待确认' }} ·
          {{ coverageLabel(source.coverage) }}
          <p class="ci-notice">
            已检查 {{ source.checked_count }} · 已采集 {{ source.captured_count }} · 待检查
            {{ source.pending_count + source.detail_pending_count }} · 失败
            {{ source.failed_count }} · 附件或图片待处理 {{ source.media_pending_count }} ·
            无法自动读取详情 {{ source.unsupported_detail_count }}
          </p>
          <p class="ci-notice">
            最近检查：{{
              source.last_checked
                ? new Date(source.last_checked * 1000).toLocaleString('zh-CN')
                : '尚无'
            }}；最近完整检查：{{
              source.last_complete
                ? new Date(source.last_complete * 1000).toLocaleString('zh-CN')
                : '尚无'
            }}
          </p>
          <p v-if="(source.issue_count ?? 0) > (source.issues?.length ?? 0)" class="ci-notice">
            本次发现 {{ source.issue_count }} 条通知有缺口，以下列出
            {{ source.issues?.length ?? 0 }}
            条可逐条重查。可使用「重新检查」继续检查其余通知。
          </p>
          <ul v-if="source.issues?.length">
            <li v-for="issue in source.issues" :key="issue.key">
              第 {{ issue.notice_index }} 条通知
              <span v-if="issue.detail_pending"> · 详情待重查</span>
              <span v-if="issue.media_pending_count">
                · 媒体待收 {{ issue.media_pending_count }}</span
              >
              <button
                type="button"
                class="ci-btn ci-text"
                :disabled="busy || !status.enabled"
                @click="recheckNotice(issue.key)"
              >
                重查此条
              </button>
            </li>
          </ul>
        </div>
      </details>
    </div>
    <div class="ci-actions">
      <button
        type="button"
        class="ci-btn ci-primary"
        :disabled="
          busy || !status || (status.enabled && !['error', 'expired'].includes(status.state))
        "
        @click="perform('connect', { visible: false })"
      >
        扫码连接 / 恢复
      </button>
      <button
        type="button"
        class="ci-btn"
        :disabled="busy || !status"
        @click="perform('connect', { visible: true })"
      >
        打开学习通官方窗口
      </button>
      <button
        type="button"
        class="ci-btn"
        :disabled="busy || !status?.enabled"
        @click="perform('pause')"
      >
        暂停采集
      </button>
      <button
        type="button"
        class="ci-btn"
        :disabled="busy || !status?.enabled"
        @click="perform('recheck')"
      >
        重新检查
      </button>
      <button type="button" class="ci-btn" :disabled="busy || !status" @click="disconnect">
        断开并清除登录
      </button>
      <a class="ci-btn" href="/api/connectors/chaoxing/report" download>导出同步检查报告</a>
    </div>
    <p v-if="error" class="ci-inlineerror" role="alert">{{ error }}</p>
    <p v-if="status?.error" class="ci-inlineerror" role="alert">{{ status.error }}</p>
    <p v-if="refreshError" class="ci-inlineerror" role="alert">
      状态读取失败{{ status ? '，当前显示上次结果' : '' }}：{{ refreshError }}
    </p>
    <div v-if="status?.qr_ready" class="ci-chaoxing-qr">
      <img
        v-if="!qrError"
        :src="`/api/connectors/chaoxing/qr?v=${qrVersion}`"
        alt="学习通登录二维码"
        @error="qrError = true"
      />
      <p v-else class="ci-inlineerror">二维码暂不可用，稍后会重新检查。</p>
      <p class="ci-notice">请使用学习通扫码登录，不要将二维码分享给他人。</p>
    </div>
    <details v-if="status?.task_sections?.length" class="ci-chaoxing-events">
      <summary>作业与考试栏目检查 · {{ status.task_sections.length }} 个</summary>
      <p class="ci-notice">显示本次连接发现的栏目及读取结果。附件和正文缺口另见待处理列表。</p>
      <div v-for="section in status.task_sections" :key="section.key" class="ci-before">
        {{ section.course || `课程 ${section.course_index}` }} ·
        {{ section.kind === 'exam' ? '考试' : '作业' }} · {{ sectionNames[section.state] }}
        <button
          type="button"
          class="ci-btn ci-text"
          :disabled="
            busy || !status.enabled || status.freshness !== 'fresh' || section.state === 'queued'
          "
          @click="recheckSection(section.key)"
        >
          重查栏目
        </button>
        <button
          type="button"
          class="ci-btn ci-text"
          :disabled="taskListBusy"
          @click="loadTaskList(section.key)"
        >
          {{ selectedSection === section.key ? '刷新条目' : '查看条目' }}
        </button>
        <div v-if="selectedSection === section.key">
          <p v-if="taskListError" class="ci-inlineerror" role="alert">{{ taskListError }}</p>
          <p v-else-if="taskListBusy && !listedTasks.length">正在读取条目…</p>
          <p v-else-if="!listedTasks.length">本次连接尚未记录此栏目的条目。</p>
          <ul v-else>
            <li v-for="task in listedTasks" :key="task.key">
              {{ task.title }} ·
              {{
                task.state === 'read'
                  ? '已读取说明'
                  : task.state === 'queued'
                    ? '已请求补收'
                    : '已发现条目'
              }}
              <button
                type="button"
                class="ci-btn ci-text"
                :disabled="
                  taskListBusy ||
                  !status.enabled ||
                  status.freshness !== 'fresh' ||
                  task.state === 'queued'
                "
                @click="recheckTask(task)"
              >
                补收这条
              </button>
            </li>
          </ul>
        </div>
      </div>
    </details>
    <div v-if="events.length" class="ci-chaoxing-events">
      <h3>需要处理的消息与附件</h3>
      <p class="ci-notice">最多显示最近 100 条；发布时间不明确时，不会自动推测。</p>
      <details v-for="event in events" :key="event.id" class="ci-chaoxing-event">
        <summary>
          {{ eventNames[event.state] }} · {{ event.title || event.course || '学习通消息' }}
        </summary>
        <p v-if="event.course" class="ci-notice">课程：{{ event.course }}</p>
        <p v-if="event.display_time" class="ci-notice">
          平台显示的时间：{{ event.display_time }}（仅供核对）
        </p>
        <p class="ci-chaoxing-text">{{ event.text || '没有可显示的正文，请在学习通中核对。' }}</p>
        <div v-for="attachment in event.attachments" :key="attachment.index" class="ci-before">
          <a
            v-if="attachment.available"
            class="ci-attachmentlink"
            :href="`/api/connectors/chaoxing/events/${event.id}/attachments/${attachment.index}`"
            >📎 下载附件：{{ attachment.name }}</a
          >
          <p v-else class="ci-notice">附件尚未下载：{{ attachment.name }}</p>
          <button
            v-if="attachment.fetchable"
            type="button"
            class="ci-btn"
            :disabled="busy"
            @click="fetchOriginal(event.id, attachment.index)"
          >
            下载并保存原件
          </button>
        </div>
        <form
          v-if="event.state === 'needs_time'"
          @submit.prevent="resolve(event.id, times[event.id] || '')"
        >
          <label class="ci-field">
            填写真实发布时间（本机时区：{{ timezone }}）
            <input v-model="times[event.id]" class="ci-input" type="datetime-local" required />
          </label>
          <button type="submit" class="ci-btn ci-primary" :disabled="busy">确认时间并处理</button>
        </form>
        <template v-else-if="event.state !== 'done'">
          <p v-if="event.state === 'needs_review'" class="ci-notice">
            请在学习通中核对内容；重试不会补齐缺失的正文。
          </p>
          <button type="button" class="ci-btn" :disabled="busy" @click="retry(event.id)">
            重试处理
          </button>
        </template>
        <p v-else class="ci-notice">
          原通知已进入收件箱。保存附件不会自动创建或修改任务，重新识别仍需到收件箱核对。
        </p>
      </details>
    </div>
    <details class="ci-chaoxing-events">
      <summary>网页组件</summary>
      <p class="ci-notice">
        需要从日常浏览器的学习通页面同步时，下载并解压组件，在 Edge 或 Chrome
        的扩展管理页加载解压目录。将本机令牌填入组件设置。重新生成令牌后，组件需要更新配对。
      </p>
      <div class="ci-actions">
        <a class="ci-btn" href="/api/connectors/chaoxing/browser/download" download>下载网页组件</a>
        <button type="button" class="ci-btn" :disabled="browserBusy" @click="browserPair()">
          生成配对令牌
        </button>
        <button
          type="button"
          class="ci-btn ci-text"
          :disabled="browserBusy"
          @click="browserPair(true)"
        >
          撤销网页组件配对
        </button>
      </div>
      <label v-if="browserToken" class="ci-field">
        网页组件令牌<input class="ci-input" readonly :value="browserToken" />
      </label>
      <p v-if="browserNote" role="status">{{ browserNote }}</p>
    </details>
  </section>
</template>
