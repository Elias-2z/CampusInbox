<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  state,
  error,
  loading,
  connected,
  initialize,
  refresh,
  changeStatus,
  request,
  setToken,
} from './api'
import {
  priorityTasks,
  filterTasks,
  formatDeadline,
  overdue,
  reminderItems,
  statusNames,
  type Task,
  type TaskStatus,
} from './domain'
import TaskList from './components/TaskList.vue'
import LegacyWorkspace from './components/LegacyWorkspace.vue'
import CalendarView from './components/CalendarView.vue'
import SettingsView from './components/SettingsView.vue'
import NewTaskModal from './components/NewTaskModal.vue'
const pages = [
  { id: 'today', icon: '☀️', label: '今天', title: '嗨，同学！开个好头 ✌️' },
  { id: 'inbox', icon: '📮', label: '收件箱', title: '通知变任务，先核对一下' },
  { id: 'tasks', icon: '🗂️', label: '我的任务', title: '把每件事，都安排明白' },
  { id: 'calendar', icon: '🗓️', label: '日历', title: '截止日期，一眼有数' },
  { id: 'reminders', icon: '🔔', label: '提醒', title: '别让重要的事溜走' },
  { id: 'sync', icon: '🔌', label: '学习通同步', title: '让课程消息，来到同一个地方' },
  { id: 'mobile', icon: '📱', label: '手机连接', title: '电脑与手机，保持连接' },
  { id: 'settings', icon: '⚙️', label: '设置', title: '按你的习惯来' },
]
const connectionPages = ['sync', 'mobile']
const navigationPages = pages.filter((page) => !connectionPages.includes(page.id))
function validPage(v: string) {
  return pages.some((p) => p.id === v) ? v : 'today'
}
function saved(key: string): Record<string, number> {
  try {
    return JSON.parse(localStorage.getItem(key) || '{}')
  } catch {
    return {}
  }
}
const view = ref(validPage(location.hash.slice(1)))
const now = ref(new Date())
const search = ref('')
const course = ref('')
const status = ref('active')
const toast = ref('')
const accessToken = ref('')
const busy = ref(false)
const workspace = ref<InstanceType<typeof LegacyWorkspace> | null>(null)
const panelFrame = ref<InstanceType<typeof LegacyWorkspace> | null>(null)
const creating = ref(false)
const panel = ref<{ kind: string; taskId?: string; messageId?: string } | null>(null)
const change = ref<{ task: Task; status: TaskStatus } | null>(null)
const undo = ref<{ task: Task; status: TaskStatus } | null>(null)
const lead = ref(Number(localStorage.getItem('ci-ui-reminder-lead')) || 1440)
const read = ref(saved('ci-ui-reminder-read'))
const snoozed = ref(saved('ci-ui-reminder-snooze'))
const currentPage = computed(() => pages.find((p) => p.id === view.value)!)
const tasks = computed(() => state.value?.tasks || [])
const active = computed(() => filterTasks(tasks.value, { status: 'active' }, now.value))
const priority = computed(() => priorityTasks(tasks.value, now.value))
const courses = computed(() => [...new Set(tasks.value.map((t) => t.course).filter(Boolean))])
const filtered = computed(() =>
  filterTasks(
    tasks.value,
    { search: search.value, course: course.value, status: status.value },
    now.value,
  ),
)
const reminders = computed(() =>
  reminderItems(tasks.value, now.value, lead.value).filter(
    (t) => (snoozed.value[reminderKey(t)] || 0) <= now.value.getTime(),
  ),
)
const unread = computed(() => reminders.value.filter((t) => !read.value[reminderKey(t)]).length)
const connectionPage = computed(() => connectionPages.includes(view.value))
const legacyPage = computed(() => view.value === 'inbox' || connectionPage.value)
const dateLabel = computed(() =>
  now.value.toLocaleDateString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  }),
)
let timer: number | undefined
let refreshRunning = false
function clearToast() {
  toast.value = ''
  undo.value = null
}
function showError(e: unknown) {
  error.value = e instanceof Error ? e.message : String(e)
}
function navigate(next: string) {
  next = validPage(next)
  if (next === view.value) return
  if (workspace.value?.hasUnsaved() && !window.confirm('审核内容尚未保存，仍要切换页面吗？')) return
  view.value = next
  location.hash = next
  window.scrollTo({ top: 0 })
}
function hashChanged() {
  const next = validPage(location.hash.slice(1))
  if (next !== view.value) {
    if (workspace.value?.hasUnsaved() && !window.confirm('审核内容尚未保存，仍要切换页面吗？')) {
      history.replaceState(null, '', '#' + view.value)
      return
    }
    view.value = next
  }
}
function openTask(task: Task) {
  panel.value = { kind: 'task', taskId: task.id }
}
function reviewCreated() {
  creating.value = false
  navigate('inbox')
}
function manualCreated(task: Task) {
  creating.value = false
  navigate('tasks')
  openTask(task)
}
async function closePanel() {
  try {
    await refresh()
  } catch (e) {
    showError(e)
  } finally {
    panel.value = null
  }
}
async function runRefresh() {
  try {
    await refresh()
    workspace.value?.refresh()
  } catch (e) {
    showError(e)
  }
}
function receive(e: MessageEvent) {
  if (e.origin !== location.origin || !e.data || typeof e.data.type !== 'string') return
  const fromPanel = panelFrame.value?.isSource(e.source)
  if (!fromPanel && !workspace.value?.isSource(e.source)) return
  if (e.data.type === 'campusinbox:changed') void refresh().catch(showError)
  if (e.data.type === 'campusinbox:close' && fromPanel) void closePanel()
  if (e.data.type === 'campusinbox:dialog-open' && !fromPanel) workspace.value?.revealDialog()
  if (e.data.type === 'campusinbox:navigate' && typeof e.data.view === 'string') {
    if (fromPanel) panel.value = null
    navigate(e.data.view)
  }
}
async function applyStatus(task: Task, next: TaskStatus) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const updated = await changeStatus(task, next)
    undo.value = { task: updated, status: task.status }
    toast.value = next === 'done' ? '已记为做完，仍需在原平台提交。' : '任务状态已更新。'
    change.value = null
  } catch (e) {
    showError(e)
  } finally {
    busy.value = false
  }
}
async function undoStatus() {
  if (!undo.value) return
  const prior = undo.value
  await applyStatus(prior.task, prior.status)
  undo.value = null
}
function chooseStatus(task: Task, next: TaskStatus) {
  if (next === 'submitted' || next === 'cancelled') change.value = { task, status: next }
  else void applyStatus(task, next)
}
function reminderKey(t: Task) {
  return `${t.id}:${t.deadline}`
}
function markRead(t: Task) {
  const key = reminderKey(t)
  if (read.value[key]) delete read.value[key]
  else read.value[key] = Date.now()
  localStorage.setItem('ci-ui-reminder-read', JSON.stringify(read.value))
}
function snooze(t: Task) {
  snoozed.value[reminderKey(t)] = Date.now() + 1800000
  localStorage.setItem('ci-ui-reminder-snooze', JSON.stringify(snoozed.value))
}
function restoreSnoozed() {
  snoozed.value = {}
  localStorage.removeItem('ci-ui-reminder-snooze')
}
function saveLead() {
  localStorage.setItem('ci-ui-reminder-lead', String(lead.value))
}
async function enableNotifications() {
  if (!('Notification' in window)) {
    error.value = '当前浏览器不支持系统通知'
    return
  }
  if ((await Notification.requestPermission()) === 'granted') {
    localStorage.setItem('courseinbox-reminders', 'enabled')
    toast.value = '已开启页面运行期间的浏览器提醒。'
    notify()
  } else toast.value = '浏览器通知未授权，站内提醒仍可使用。'
}
function notify() {
  if (
    !('Notification' in window) ||
    Notification.permission !== 'granted' ||
    localStorage.getItem('courseinbox-reminders') !== 'enabled'
  )
    return
  for (const t of reminders.value) {
    const level = overdue(t, now.value)
      ? 'overdue'
      : t.deadline?.length === 10
        ? 'day'
        : Date.parse(t.deadline!) - now.value.getTime() <= 7200000
          ? 'soon'
          : 'today'
    const key = `ci-ui-notified:${reminderKey(t)}:${level}`
    if (localStorage.getItem(key)) continue
    try {
      const n = new Notification(t.title, {
        body: `${t.course} · ${formatDeadline(t.deadline)}`,
        tag: key,
      })
      n.onclick = () => {
        window.focus()
        openTask(t)
        n.close()
      }
      localStorage.setItem(key, '1')
    } catch {
      /* Permissions may change in browser settings. */
    }
  }
}
async function tick() {
  now.value = new Date()
  if (document.visibilityState !== 'visible' || !connected.value || refreshRunning) return
  refreshRunning = true
  try {
    await refresh()
    notify()
  } catch (e) {
    showError(e)
  } finally {
    refreshRunning = false
  }
}
async function connect() {
  setToken(accessToken.value)
  try {
    await refresh()
  } catch (e) {
    showError(e)
  }
}
async function loadDemo() {
  busy.value = true
  try {
    await request('/demo', { method: 'POST', body: '{}' })
    await refresh()
    toast.value = '已加载明确标注的示例数据。'
  } catch (e) {
    showError(e)
  } finally {
    busy.value = false
  }
}
onMounted(async () => {
  await initialize()
  const q = new URLSearchParams(location.search)
  const task = q.get('task'),
    message = q.get('message')
  if (task && /^[a-f0-9]{32}$/.test(task)) panel.value = { kind: 'task', taskId: task }
  else if (message && /^[a-f0-9]{32}$/.test(message))
    panel.value = { kind: 'message', messageId: message }
  window.addEventListener('hashchange', hashChanged)
  window.addEventListener('message', receive)
  window.addEventListener('focus', tick)
  timer = window.setInterval(tick, 15000)
})
onUnmounted(() => {
  window.clearInterval(timer)
  window.removeEventListener('hashchange', hashChanged)
  window.removeEventListener('message', receive)
  window.removeEventListener('focus', tick)
})
</script>
<template>
  <div class="app-root">
    <header class="ci-top">
      <a href="#today" class="ci-brand" @click.prevent="navigate('today')"
        ><span class="ci-mark">↗</span>CampusInbox<span class="ci-branddot">.</span></a
      >
      <nav aria-label="主导航">
        <button
          v-for="page in navigationPages"
          :key="page.id"
          class="ci-nav"
          :aria-current="
            view === page.id || (page.id === 'settings' && connectionPage) ? 'page' : undefined
          "
          @click="navigate(page.id)"
        >
          {{ page.icon }} {{ page.label
          }}<span v-if="page.id === 'inbox' && state?.proposals.length" class="ci-navcount">{{
            state.proposals.length
          }}</span
          ><span v-if="page.id === 'reminders' && unread" class="ci-navcount">{{ unread }}</span>
        </button>
      </nav>
      <button class="ci-btn ci-refresh" aria-label="刷新数据" @click="runRefresh">↻</button>
    </header>
    <div v-if="error" role="alert" class="ci-status ci-errorbanner">
      <span>{{ error }}</span
      ><button class="ci-btn" @click="runRefresh">重试</button>
    </div>
    <div v-if="toast" role="status" class="ci-status">
      <span>{{ toast }}</span
      ><button v-if="undo" class="ci-btn" :disabled="busy" @click="undoStatus">撤销</button
      ><button class="ci-btn" aria-label="关闭提示" @click="clearToast">×</button>
    </div>
    <main class="ci-main">
      <div class="ci-pagehead">
        <div>
          <button v-if="connectionPage" class="ci-btn ci-text" @click="navigate('settings')">
            ← 返回设置
          </button>
          <div class="ci-eyebrow">{{ dateLabel }}</div>
          <h1>{{ currentPage.title }}</h1>
        </div>
        <button
          v-if="view === 'tasks'"
          class="ci-btn ci-primary"
          :disabled="!connected"
          @click="creating = true"
        >
          ＋ 新建任务
        </button>
      </div>
      <p v-if="loading" role="status" class="ci-notice">正在打开你的校园清单…</p>
      <form v-if="!connected && !loading" class="ci-connect" @submit.prevent="connect">
        <h2>连接 CampusInbox</h2>
        <p>远程访问需要电脑端的访问令牌。</p>
        <label
          >访问令牌<input
            v-model="accessToken"
            type="password"
            class="ci-input"
            autocomplete="off"
            required /></label
        ><button class="ci-btn ci-primary">连接</button>
      </form>
      <template v-if="connected && state">
        <template v-if="view === 'today'">
          <section class="ci-hero">
            <div>
              <h2>把琐事放这里，<br />把脑袋留给好主意。</h2>
              <p>
                {{
                  active.length
                    ? `还有 ${active.length} 件事等你，先从最重要的开始。`
                    : '从一条课程通知开始，把事情安排明白。'
                }}
              </p>
            </div>
            <div class="ci-hero-art" aria-hidden="true">
              <div class="ci-orbit" />
              <span class="ci-sun">🌞</span><span class="ci-sticker">YOU GOT THIS!</span>
            </div>
          </section>
          <div class="ci-metrics">
            <div>
              <span>尚未提交</span><strong>{{ active.length }}</strong>
            </div>
            <div>
              <span>未来七天</span><strong>{{ state.summary.upcoming }}</strong>
            </div>
            <div>
              <span>已逾期</span><strong>{{ state.summary.overdue }}</strong>
            </div>
            <div>
              <span>已确认提交</span><strong>{{ state.summary.submitted }}</strong>
            </div>
          </div>
          <div class="ci-columns">
            <section>
              <div class="ci-sectionhead">
                <h2>
                  接下来先搞定 👇 <span class="ci-count">{{ active.length }} 项待办</span>
                </h2>
                <button class="ci-btn ci-text" @click="navigate('tasks')">全部任务 ↗</button>
              </div>
              <TaskList
                v-if="priority.length"
                :tasks="priority"
                :now="now"
                @open="openTask"
                @status="chooseStatus"
              />
              <div v-else class="ci-first">
                <span class="ci-firstemoji">🌱</span>
                <h2>{{ tasks.length ? '待办已经清空' : '你的校园清单，从这里生长' }}</h2>
                <p>粘贴一条老师的通知，核对后就能变成任务。</p>
                <div class="ci-actions">
                  <button class="ci-btn ci-primary" @click="navigate('tasks')">
                    前往我的任务 →</button
                  ><button
                    v-if="!tasks.length && !state.messages.length"
                    class="ci-btn"
                    :disabled="busy"
                    @click="loadDemo"
                  >
                    试用示例数据
                  </button>
                </div>
              </div>
              <div v-if="tasks.length" class="ci-donebar">
                <span>已提交 {{ state.summary.submitted }} / 未提交 {{ active.length }}</span>
                <div class="ci-progress">
                  <span
                    :style="{
                      width: `${(state.summary.submitted / Math.max(1, state.summary.submitted + active.length)) * 100}%`,
                    }"
                  />
                </div>
                <span>🌱</span>
              </div>
            </section>
            <aside class="ci-side">
              <section class="ci-inboxtease">
                <div class="ci-teaseicon">📮</div>
                <h3>
                  {{
                    state.proposals.length
                      ? `${state.proposals.length} 条建议，等你拍板`
                      : '收件箱，替你留住重点'
                  }}
                </h3>
                <p>新任务、延期、材料变更与冲突，都先由你核对。</p>
                <button class="ci-btn ci-primary" @click="navigate('inbox')">去收件箱看看 ↗</button>
              </section>
              <section class="ci-inboxtease ci-reminder-tease">
                <div class="ci-teaseicon">🔔</div>
                <h3>{{ reminders.length }} 项截止提醒</h3>
                <p>已做完还不等于已提交，交付前再核对一次。</p>
                <button class="ci-btn" @click="navigate('reminders')">查看提醒 ↗</button>
              </section>
              <button class="ci-btn" @click="panel = { kind: 'assistant' }">✦ 问问课务助手</button>
            </aside>
          </div>
          <section v-if="state.messages.length" class="ci-recent">
            <div class="ci-sectionhead">
              <h2>最近收到</h2>
              <button class="ci-btn ci-text" @click="navigate('inbox')">全部通知 ↗</button>
            </div>
            <button
              v-for="message in state.messages.slice(0, 3)"
              :key="message.id"
              class="ci-message-preview"
              @click="panel = { kind: 'message', messageId: message.id }"
            >
              <span
                >{{ message.source }}
                <span v-if="message.sample" class="ci-sample">示例</span></span
              >
              <p>{{ message.text.slice(0, 100) }}</p>
              <b>↗</b>
            </button>
          </section>
        </template>
        <template v-else-if="view === 'tasks'"
          ><div class="ci-filters">
            <input
              v-model="search"
              class="ci-input"
              placeholder="搜索任务、课程或班级"
              aria-label="搜索任务"
            /><select v-model="course" class="ci-input" aria-label="筛选课程">
              <option value="">全部课程</option>
              <option v-for="c in courses" :key="c">{{ c }}</option></select
            ><select v-model="status" class="ci-input" aria-label="筛选状态">
              <option value="active">尚未提交</option>
              <option value="overdue">已逾期</option>
              <option value="all">全部任务</option>
              <option v-for="(name, key) in statusNames" :key="key" :value="key">
                {{ name }}
              </option></select
            ><button class="ci-btn" @click="panel = { kind: 'assistant' }">✦ 课务助手</button>
          </div>
          <p class="ci-muted ci-results">显示 {{ filtered.length }} / {{ tasks.length }} 项任务</p>
          <TaskList :tasks="filtered" :now="now" @open="openTask" @status="chooseStatus" />
          <div v-if="!filtered.length" class="ci-first">
            <h2>这里暂时没有任务</h2>
            <p>调整筛选，或导入一条通知开始。</p>
          </div></template
        >
        <CalendarView
          v-else-if="view === 'calendar'"
          :tasks="tasks"
          :now="now"
          @open="openTask"
          @error="error = $event"
        />
        <template v-else-if="view === 'reminders'"
          ><div class="ci-filters">
            <label
              >提前提醒<select v-model="lead" class="ci-input" @change="saveLead">
                <option :value="60">1 小时</option>
                <option :value="120">2 小时</option>
                <option :value="1440">1 天</option>
                <option :value="4320">3 天</option>
              </select></label
            ><button class="ci-btn ci-primary" @click="enableNotifications">开启浏览器通知</button
            ><button class="ci-btn" @click="restoreSnoozed">恢复稍后提醒</button
            ><button class="ci-btn" @click="navigate('mobile')">手机与后台推送 ↗</button>
          </div>
          <p class="ci-notice">
            网页打开时检查；未写时刻的任务按“仅日期”显示。已读与稍后状态保存在当前浏览器。
          </p>
          <article
            v-for="task in reminders"
            :key="reminderKey(task)"
            class="ci-reminder-item"
            :class="{ 'ci-read': read[reminderKey(task)] }"
          >
            <div>
              <span class="ci-tag">{{ overdue(task, now) ? '已逾期' : '即将截止' }}</span
              ><button class="ci-tasktitle" @click="openTask(task)">{{ task.title }}</button>
              <p>{{ task.course }} · {{ formatDeadline(task.deadline) }}</p>
            </div>
            <div class="ci-actions">
              <button class="ci-btn" @click="markRead(task)">
                {{ read[reminderKey(task)] ? '恢复未读' : '标为已读' }}</button
              ><button class="ci-btn" @click="snooze(task)">稍后 30 分钟</button>
            </div>
          </article>
          <div v-if="!reminders.length" class="ci-first">
            <h2>暂时没有截止提醒 🌱</h2>
            <p>任务进入提醒范围时，会出现在这里。</p>
          </div></template
        >
        <SettingsView v-else-if="view === 'settings'" @navigate="navigate" />
        <LegacyWorkspace v-else-if="legacyPage" :key="view" ref="workspace" :view="view" />
      </template>
    </main>
    <footer class="ci-footer">
      <span>CampusInbox · 本地校园任务助手</span
      ><span
        ><button class="ci-footer-link" @click="panel = { kind: 'assistant' }">课务助手</button> ·
        <a href="/legacy.html" target="_blank" rel="noopener">完整工作区</a></span
      >
    </footer>
    <NewTaskModal
      v-if="creating"
      @close="creating = false"
      @review="reviewCreated"
      @created="manualCreated"
    />
    <div
      v-if="panel"
      class="ci-modal-shade"
      role="dialog"
      aria-modal="true"
      aria-label="完整任务操作"
    >
      <div class="ci-modal ci-integrated-panel">
        <LegacyWorkspace
          :key="JSON.stringify(panel)"
          ref="panelFrame"
          view="tasks"
          :panel="panel.kind"
          :task-id="panel.taskId"
          :message-id="panel.messageId"
        />
      </div>
    </div>
    <div
      v-if="change"
      class="ci-modal-shade"
      role="dialog"
      aria-modal="true"
      aria-labelledby="status-confirm-title"
    >
      <div class="ci-modal ci-confirm">
        <h2 id="status-confirm-title">确认记为{{ statusNames[change.status] }}？</h2>
        <p>{{ change.task.title }}</p>
        <p class="ci-muted">这是本地记录，不会代替你在原平台提交作业。</p>
        <div class="ci-actions">
          <button class="ci-btn" :disabled="busy" @click="change = null">返回</button
          ><button
            class="ci-btn ci-primary"
            :disabled="busy"
            @click="applyStatus(change.task, change.status)"
          >
            确认
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
