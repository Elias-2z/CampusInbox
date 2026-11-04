<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import {
  planLegacyMigration,
  importLegacyMigration,
  type MigrationPlan,
  type MigrationResult,
} from '@/legacyPreview'
import { useCampus } from '@/stores/campus'

const file = ref<File | null>(null)
const result = ref<MigrationPlan | null>(null)
const imported = ref<MigrationResult | null>(null)
const namespace = ref('local-assistant')
const confirmed = ref(false)
const store = useCampus()
const error = ref('')
const working = ref(false)
let revision = 0
onUnmounted(() => {
  revision++
})
function invalidate() {
  revision++
  result.value = null
  imported.value = null
  confirmed.value = false
  error.value = ''
}
function selectFile(event: Event) {
  invalidate()
  file.value = (event.target as HTMLInputElement).files?.[0] || null
}
async function preview() {
  if (!file.value || working.value) return
  const current = revision
  working.value = true
  error.value = ''
  result.value = null
  try {
    confirmed.value = false
    imported.value = null
    const value = await planLegacyMigration(file.value, namespace.value)
    if (current === revision) result.value = value
  } catch (cause) {
    if (current === revision)
      error.value = cause instanceof Error ? cause.message : '检查失败，请重试。'
  } finally {
    working.value = false
  }
}

async function migrate() {
  if (!file.value || !result.value?.can_import || !confirmed.value || working.value) return
  const current = revision
  working.value = true
  error.value = ''
  try {
    const receipt = await importLegacyMigration(
      file.value,
      namespace.value,
      result.value.plan_token,
    )
    if (current !== revision) return
    imported.value = receipt
    result.value = null
    confirmed.value = false
    try {
      await store.refresh()
    } catch {
      error.value = '迁移已完成，但列表刷新失败，请刷新页面查看；不要更换数据集名称重复迁移。'
    }
  } catch (cause) {
    if (current === revision) {
      result.value = null
      confirmed.value = false
      error.value =
        cause instanceof Error ? cause.message : '迁移未确认完成，请使用相同数据集重新检查。'
    }
  } finally {
    working.value = false
  }
}
</script>

<template>
  <section class="ci-form ci-reminder-settings" aria-label="旧版数据迁移检查">
    <h3>📦 旧版数据迁移检查</h3>
    <p class="ci-notice ci-before">
      先选择旧版 JSON 或包含原件的迁移
      ZIP。“检查文件”只检查，不会导入或修改数据；核对后可明确确认导入。
    </p>
    <label class="ci-field"
      >旧版导出或迁移包<input
        type="file"
        accept=".json,.zip"
        :disabled="working"
        @change="selectFile"
    /></label>
    <label class="ci-field"
      >数据集名称
      <input v-model="namespace" :disabled="working" maxlength="64" @input="invalidate" />
      <small>同一份旧数据始终使用相同名称，重复迁移会跳过相同记录。不要用改名绕过冲突。</small>
    </label>
    <button
      type="button"
      class="ci-btn cursor-interaction"
      :disabled="!file || working"
      @click="preview"
    >
      {{ working ? '正在检查…' : '检查文件' }}
    </button>
    <p v-if="error" class="ci-inlineerror" role="alert">{{ error }}</p>
    <div v-if="result" class="ci-before" role="status">
      <p>
        通知 {{ result.counts.messages }} · 建议 {{ result.counts.proposals }} · 任务
        {{ result.counts.tasks }} · 历史 {{ result.counts.history }} · 材料
        {{ result.counts.materials }}
      </p>
      <p>
        发送时间未知 {{ result.issues.unknown_message_time }} · 多来源任务
        {{ result.issues.multi_source_tasks }} · 多任务共享通知
        {{ result.issues.messages_shared_by_tasks }} · 任务缺少来源
        {{ result.issues.missing_task_sources }}
      </p>
      <p>
        重复通知编号 {{ result.issues.duplicate_message_ids }} · 重复任务编号
        {{ result.issues.duplicate_task_ids }} · 缺少通知编号
        {{ result.issues.missing_message_ids }} · 缺少任务编号 {{ result.issues.missing_task_ids }}
      </p>
      <p>通知中记录的附件引用：{{ result.issues.attachment_references }} 项。</p>
      <p class="ci-notice">
        旧版 JSON 不包含附件原文件及全部本机设置。ZIP
        仅迁移清单中校验通过的原件；保留旧版完整数据目录。
      </p>
      <p>
        新增：通知 {{ result.new.messages }}、任务 {{ result.new.tasks }}、建议
        {{ result.new.proposals }}； 已迁移任务 {{ result.skipped.tasks }}；同编号内容冲突
        {{ Object.values(result.conflicts).reduce((a, b) => a + b, 0) }}。
      </p>
      <p>
        可用原件 {{ result.originals.available }}，缺少原件 {{ result.originals.missing }}。
        主项目可能同名同课程任务 {{ result.possible_task_collisions }}；不会自动合并。
      </p>
      <p v-if="!result.can_import" class="ci-inlineerror">
        存在内容冲突，不能导入。请核对原数据，已有任务不会被覆盖。
      </p>
      <label v-if="result.can_import" class="ci-field">
        <span
          ><input v-model="confirmed" type="checkbox" :disabled="working" />
          我已核对来源、未知时间、缺失原件及可能重复任务，确认迁入主项目。旧系统不再用于修改这些记录。</span
        >
      </label>
      <button
        type="button"
        class="ci-btn ci-primary"
        :disabled="working || !confirmed || !result.can_import"
        @click="migrate"
      >
        {{ working ? '正在备份并迁移…' : '备份并确认导入' }}
      </button>
    </div>
    <p v-if="imported" class="ci-notice" role="status">
      迁移完成：新增任务 {{ imported.imported.tasks }}、通知 {{ imported.imported.messages }}。
      {{
        imported.backup ? '迁移前数据库备份：' + imported.backup : '相同记录已跳过，无需重复写入。'
      }}
    </p>
  </section>
</template>
