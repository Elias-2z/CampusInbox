<script setup lang="ts">
import { onMounted, ref } from 'vue'
defineProps<{ title: string; locked?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
onMounted(() => dialog.value?.showModal())
</script>
<template>
  <dialog
    ref="dialog"
    class="ci-modal"
    @cancel.prevent="!locked && emit('close')"
    @click="
      (event) => {
        if (event.target === dialog && !locked) emit('close')
      }
    "
  >
    <div class="ci-modalbody">
      <div class="ci-sectionhead">
        <h2>{{ title }}</h2>
        <button
          class="ci-btn ci-text cursor-interaction"
          :disabled="locked"
          aria-label="关闭对话框"
          @click="emit('close')"
        >
          关闭 ✕
        </button>
      </div>
      <slot />
    </div>
  </dialog>
</template>
