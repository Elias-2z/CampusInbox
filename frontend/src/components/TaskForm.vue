<script setup lang="ts">
import type { Evidence, TaskInput } from '@/api'
const model = defineModel<TaskInput>({ required: true })
defineProps<{ busy?: boolean; evidence?: Record<string, Evidence> }>()
const provenance = (field: Evidence | undefined) =>
  !field
    ? ''
    : `${field.method === 'connector' ? '群课程设置' : field.method === 'rule' ? '规则依据' : field.method === 'user' ? '用户填写' : '模型辅助'}：${field.quote}`
</script>
<template>
  <fieldset class="ci-fieldset" :disabled="busy">
    <label class="ci-field"
      >任务名称 <span class="ci-required">必填</span>
      <input
        v-model="model.title"
        class="ci-input"
        required
        maxlength="160"
        placeholder="这件事叫什么？"
      />
      <small v-if="evidence?.title" class="ci-evidence">{{ provenance(evidence.title) }}</small>
    </label>
    <div class="ci-fieldrow">
      <label class="ci-field"
        >课程 / 分类<input
          v-model="model.course"
          class="ci-input"
          maxlength="100"
          placeholder="可以稍后补充"
      /></label>
      <label class="ci-field"
        >任务类型<select v-model="model.kind" class="ci-input">
          <option value="other">其他</option>
          <option value="homework">作业 / 实验</option>
          <option value="exam">考试</option>
          <option value="event">活动</option>
        </select></label
      >
    </div>
    <label class="ci-field"
      >截止时间<input v-model="model.due_at" type="datetime-local" class="ci-input" />
      <small class="ci-evidence">{{
        evidence?.due_at ? provenance(evidence.due_at) : '没有明确时间可以留空，之后再补充。'
      }}</small>
    </label>
    <div class="ci-fieldrow">
      <label class="ci-field"
        >提交平台<input
          v-model="model.submission_platform"
          class="ci-input"
          maxlength="100"
          placeholder="例如：学习通"
      /></label>
      <label class="ci-field"
        >提交方式<input
          v-model="model.submission_method"
          class="ci-input"
          maxlength="300"
          placeholder="例如：上传 PDF"
      /></label>
    </div>
    <label class="ci-field"
      >文件要求<input
        v-model="model.file_requirements"
        class="ci-input"
        maxlength="2000"
        placeholder="命名、格式、份数等"
    /></label>
    <details class="ci-morefields">
      <summary class="cursor-interaction">内容、地点与备注</summary>
      <label class="ci-field"
        >任务内容<textarea v-model="model.description" class="ci-input" maxlength="20000" />
      </label>
      <label class="ci-field"
        >地点<input v-model="model.location" class="ci-input" maxlength="300"
      /></label>
      <label class="ci-field"
        >备注<input v-model="model.remarks" class="ci-input" maxlength="2000"
      /></label>
    </details>
  </fieldset>
</template>
