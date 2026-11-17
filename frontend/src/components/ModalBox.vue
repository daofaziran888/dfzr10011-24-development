<template>
  <div v-if="open" class="modal-mask" @click.self="emit('close')">
    <section class="modal-card" :style="{ maxWidth: width }">
      <header class="modal-head">
        <h3>{{ title }}</h3>
        <button class="link" type="button" @click="emit('close')">关闭</button>
      </header>
      <div class="modal-body">
        <slot />
      </div>
      <footer v-if="$slots.footer" class="modal-foot">
        <slot name="footer" />
      </footer>
    </section>
  </div>
</template>

<script setup lang="ts">
defineProps<{ open: boolean; title: string; width?: string }>()
const emit = defineEmits<{ (event: 'close'): void }>()
</script>
