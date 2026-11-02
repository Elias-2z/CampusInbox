<script setup lang="ts">
import { onMounted, ref } from 'vue'
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
const status = ref<Status | null>(null)
const config = ref<Config>({ enabled: false, account_id: '', groups: [], revision: 0 })
const groups = ref<Group[]>([])
const members = ref<Record<string, Group[]>>({})
const busy = ref(false)
const error = ref('')
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
    <h3>🔌 数据源连接 · QQ</h3>
    <p class="ci-notice ci-before">
      {{
        status?.adapter.connected
          ? `已连接账号 ${status.adapter.account_id}`
          : '尚未连接 QQ 消息桥'
      }}。 已支持本机 OneBot HTTP 消息桥；QQ 扫码与验证在桥接程序中完成。保存检测范围不会登录 QQ。
    </p>
    <OneBotBridge @changed="load" />
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
        <div v-if="groups.length" class="ci-actions">
          <button
            v-for="group in groups"
            :key="group.id"
            type="button"
            class="ci-btn"
            @click="add(group)"
          >
            ＋ {{ group.name }}
          </button>
        </div>
        <p v-else class="ci-notice">连接通过后可读取群与成员列表；也可先保存群号和成员号。</p>
        <div v-for="(group, index) in config.groups" :key="index" class="ci-source ci-before">
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
          <div v-if="members[group.id]?.length" class="ci-filters">
            <label v-for="member in members[group.id]" :key="member.id" class="ci-checklabel"
              ><input v-model="group.member_ids" type="checkbox" :value="member.id" />{{
                member.name
              }}（{{ member.id }}）</label
            >
          </div>
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
        </div>
        <div class="ci-actions">
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
