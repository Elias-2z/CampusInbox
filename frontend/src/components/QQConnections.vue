<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request, post } from '@/api'
import { useCampus } from '@/stores/campus'
import OneBotBridge from './OneBotBridge.vue'
interface Group {
  id: string
  name: string
}
interface Selection extends Group {
  course?: string | null
  member_ids: string[]
}
interface Config {
  enabled: boolean
  account_id: string
  groups: Selection[]
  revision: number
}
interface Status {
  config: Config
  adapter: { connected: boolean; account_id: string; name: string }
  course_suggestions: Record<string, string>
  counts: Record<string, number>
  last_received_at: string | null
  last_sync_at: string | null
  error: string
}
const store = useCampus()
const bridgePanel = ref<InstanceType<typeof OneBotBridge> | null>(null)
const status = ref<Status | null>(null)
const config = ref<Config>({ enabled: false, account_id: '', groups: [], revision: 0 })
const groups = ref<Group[]>([])
const members = ref<Record<string, Group[]>>({})
const busy = ref(false)
const error = ref('')
const groupSearch = ref('')
const memberSearch = ref<Record<string, string>>({})
const availableGroups = computed(() =>
  groups.value.filter(
    (g) =>
      !config.value.groups.some((selected) => selected.id === g.id) &&
      `${g.name} ${g.id}`.toLowerCase().includes(groupSearch.value.toLowerCase()),
  ),
)
function visibleMembers(id: string) {
  const query = (memberSearch.value[id] || '').toLowerCase()
  const selected = new Set(config.value.groups.find((g) => g.id === id)?.member_ids || [])
  return (members.value[id] || [])
    .filter((m) => `${m.name} ${m.id}`.toLowerCase().includes(query))
    .sort((a, b) => Number(selected.has(b.id)) - Number(selected.has(a.id)))
    .slice(0, 100)
}
function revealEmptyGroup(element: unknown, group: Selection) {
  if (element instanceof HTMLDetailsElement && !group.id) element.open = true
}
async function load() {
  busy.value = true
  error.value = ''
  try {
    status.value = await request<Status>('/connectors/qq')
    config.value = JSON.parse(JSON.stringify(status.value.config))
    if (!config.value.account_id && status.value.adapter.connected)
      config.value.account_id = status.value.adapter.account_id
    groups.value = await request<Group[]>('/connectors/qq/groups')
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    busy.value = false
  }
}
onMounted(load)
async function add(group: Group) {
  if (config.value.groups.length >= 50) return
  if (!config.value.groups.some((g) => g.id === group.id)) {
    const item = { ...group, course: null, member_ids: [] } as Selection
    config.value.groups.push(item)
    await inferCourse(item, false)
  }
}
async function inferCourse(group: Selection, useModel = true) {
  busy.value = true
  error.value = ''
  try {
    const result = await post<{ course: string; method: string }>(
      '/connectors/qq/course-suggestion',
      { group_name: group.name, use_model: useModel },
    )
    group.course = result.course
    store.notice = result.course
      ? '已生成课程建议，请核对后保存检测范围。'
      : '暂时无法明确课程，请手动填写或留空。'
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    busy.value = false
  }
}
async function readMembers(id: string) {
  try {
    members.value[id] = await request<Group[]>(`/connectors/qq/groups/${id}/members`)
  } catch (e) {
    error.value = store.errorMessage(e)
  }
}
async function act(action: 'save' | 'sync' | 'retry' | 'disconnect') {
  busy.value = true
  error.value = ''
  try {
    status.value =
      action === 'save'
        ? await request<Status>('/connectors/qq/settings', {
            method: 'PUT',
            body: JSON.stringify(config.value),
          })
        : await post<Status>(`/connectors/qq/${action}`)
    config.value = JSON.parse(JSON.stringify(status.value.config))
    store.notice = action === 'save' ? 'QQ 检测范围已保存。' : 'QQ 同步状态已刷新。'
    if (action !== 'save') await store.refresh()
  } catch (e) {
    error.value = store.errorMessage(e)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <section class="ci-form ci-reminder-settings" aria-label="QQ 数据源连接">
    <div class="ci-connection-cardhead">
      <h2>🐧 账号与通知范围</h2>
      <span class="ci-connection-badge" :class="{ 'is-online': status?.adapter.connected }">{{
        status?.adapter.connected ? '已连接' : '未连接'
      }}</span>
    </div>
    <p class="ci-notice ci-before">
      {{
        status?.adapter.connected
          ? `已连接账号 ${status.adapter.account_id}`
          : '尚未连接 QQ 消息桥'
      }}。选好课程群与成员后，保存即可开始检测。
    </p>
    <button
      v-if="status && !status.adapter.connected"
      type="button"
      class="ci-btn ci-primary ci-before"
      @click="bridgePanel?.open()"
    >
      连接 QQ
    </button>
    <OneBotBridge ref="bridgePanel" @changed="load" />
    <form @submit.prevent="act('save')">
      <fieldset class="ci-fieldset" :disabled="busy || !status">
        <label class="ci-field"
          >接收账号 QQ 号<input
            v-model="config.account_id"
            class="ci-input"
            inputmode="numeric"
            pattern="[1-9][0-9]{0,19}"
            placeholder="绑定账号，防止切换账号后误收消息"
        /></label>
        <label class="ci-checklabel"
          ><input
            v-model="config.enabled"
            type="checkbox"
          />启用选定范围的检测（需要消息桥在线）</label
        >
        <p class="ci-notice">
          只处理选定群；成员留空表示全群，否则仅处理选定成员。自己发送的消息跳过。
        </p>
        <h3>接收哪些课程群？</h3>
        <details v-if="groups.length" class="ci-connection-picker">
          <summary>＋ 添加课程群 · 已选择 {{ config.groups.length }} 个</summary>
          <label class="ci-field"
            >查找群<input
              v-model="groupSearch"
              class="ci-input"
              type="search"
              placeholder="搜索群名或群号"
          /></label>
          <div class="ci-connection-choices">
            <button
              v-for="group in availableGroups"
              :key="group.id"
              type="button"
              class="ci-btn"
              @click="add(group)"
            >
              ＋ {{ group.name }}
            </button>
            <p v-if="!availableGroups.length" class="ci-notice">没有匹配的未选群。</p>
          </div>
        </details>
        <p v-else class="ci-notice">连接后可选择课程群，也可以在下方手动添加。</p>
        <details
          v-for="(group, index) in config.groups"
          :key="index"
          class="ci-connection-scope"
          :ref="(el) => revealEmptyGroup(el, group)"
        >
          <summary>
            <strong>{{
              group.course || status?.course_suggestions[group.id] || group.name || '新课程群'
            }}</strong
            ><span>{{
              group.member_ids.length ? `仅 ${group.member_ids.length} 位成员` : '全群消息'
            }}</span
            ><span>编辑范围 ↓</span>
          </summary>
          <div class="ci-fieldrow">
            <label class="ci-field"
              >群号<input v-model="group.id" class="ci-input" required pattern="[1-9][0-9]{0,19}"
            /></label>
            <label class="ci-field"
              >群名称<input v-model="group.name" class="ci-input" required maxlength="100"
            /></label>
          </div>
          <label class="ci-field"
            >对应课程（自动去掉年份、班级等，可修改；留空则不补充）
            <input
              class="ci-input"
              maxlength="100"
              :value="group.course ?? status?.course_suggestions[group.id] ?? ''"
              @input="group.course = ($event.target as HTMLInputElement).value"
            />
          </label>
          <label class="ci-field"
            >限定成员 QQ 号（逗号分隔，留空为全群）<input
              :value="group.member_ids.join(',')"
              class="ci-input"
              @change="
                group.member_ids = ($event.target as HTMLInputElement).value
                  .split(/[,，\s]+/)
                  .filter(Boolean)
              "
          /></label>
          <label v-if="members[group.id]?.length" class="ci-field"
            >查找成员<input
              v-model="memberSearch[group.id]"
              class="ci-input"
              type="search"
              placeholder="搜索群名片、昵称或 QQ 号"
          /></label>
          <div v-if="members[group.id]?.length" class="ci-connection-memberlist">
            <label v-for="member in visibleMembers(group.id)" :key="member.id" class="ci-checklabel"
              ><input v-model="group.member_ids" type="checkbox" :value="member.id" />{{
                member.name
              }}（{{ member.id }}）</label
            >
          </div>
          <p v-if="members[group.id]?.length" class="ci-notice">
            每次最多显示 100 位成员，可搜索查找其他成员；已选范围会保留。
          </p>
          <div class="ci-actions">
            <button type="button" class="ci-btn" @click="inferCourse(group)">智能识别课程名</button>
            <button
              type="button"
              class="ci-btn"
              :disabled="!status?.adapter.connected"
              @click="readMembers(group.id)"
            >
              读取成员
            </button>
            <button type="button" class="ci-btn ci-text" @click="config.groups.splice(index, 1)">
              移除群
            </button>
          </div>
        </details>
        <div class="ci-actions ci-connection-save">
          <button
            type="button"
            class="ci-btn"
            :disabled="config.groups.length >= 50"
            @click="config.groups.push({ id: '', name: '', member_ids: [] })"
          >
            ＋ 添加群范围
          </button>
          <button class="ci-btn ci-primary">保存检测范围 ✓</button>
        </div>
      </fieldset>
    </form>
    <div v-if="status" class="ci-before">
      <p class="ci-notice">
        待处理 {{ status.counts.pending }} · 已送入收件箱 {{ status.counts.done }} · 失败
        {{ status.counts.failed }} · 范围外保留 {{ status.counts.blocked }}
      </p>
      <p class="ci-notice">
        最近接收：{{
          status.last_received_at ? new Date(status.last_received_at).toLocaleString() : '暂无'
        }}
        · 最近检查：{{
          status.last_sync_at ? new Date(status.last_sync_at).toLocaleString() : '暂无'
        }}
      </p>
      <p class="ci-notice">
        后端运行时约每 3
        秒检查适配器；新任务、延期和取消仍需审核。关闭网页不会停止后端，电脑休眠或后端退出时无法实时检测。
      </p>
      <div class="ci-actions">
        <button
          class="ci-btn"
          :disabled="busy || !status.adapter.connected || !config.enabled"
          @click="act('sync')"
        >
          立即检查已保存范围
        </button>
        <button
          class="ci-btn"
          :disabled="busy || !status.counts.failed || !status.adapter.connected"
          @click="act('retry')"
        >
          重试失败消息
        </button>
        <button
          class="ci-btn"
          :disabled="busy || !status.adapter.connected"
          @click="act('disconnect')"
        >
          断开连接并暂停
        </button>
        <button class="ci-btn ci-text" :disabled="busy" @click="load">重新读取设置</button>
      </div>
    </div>
    <p v-if="error || status?.error" class="ci-inlineerror" role="alert">
      {{ error || status?.error }}
    </p>
  </section>
</template>
