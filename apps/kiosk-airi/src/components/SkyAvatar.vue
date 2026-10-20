<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import type { AssistantStatus } from '../types'

const props = withDefaults(defineProps<{
  src: string
  poster?: string
  state: AssistantStatus
}>(), {
  poster: '/avatars/sky-poster.png',
})

type ModelViewerLike = HTMLElement & {
  animationName?: string
  play?: () => void
  pause?: () => void
}

const modelViewer = ref<ModelViewerLike | null>(null)

const animationName = computed(() => {
  if (props.state === 'speaking') return 'talk'
  if (props.state === 'thinking') return 'sit_talk'
  return 'idle'
})

function syncAnimation() {
  const viewer = modelViewer.value
  if (!viewer) return
  viewer.animationName = animationName.value
  if (animationName.value === 'idle') viewer.pause?.()
  else viewer.play?.()
}

watch(animationName, syncAnimation)
onMounted(async () => {
  await import('@google/model-viewer')
  syncAnimation()
})
</script>

<template>
  <section class="sky-avatar" :class="'is-' + state" aria-label="Avatar Sky">
    <div class="avatar-wrap">
      <model-viewer
        ref="modelViewer"
        class="sky-model"
        :src="src"
        :poster="poster"
        :animation-name="animationName"
        alt="Avatar Sky đang hỗ trợ mua sắm"
        camera-controls
        disable-pan
        interaction-prompt="none"
        shadow-intensity="0.7"
        exposure="1.05"
        camera-orbit="0deg 78deg auto"
        loading="eager"
        @load="syncAnimation"
      ></model-viewer>
    </div>
  </section>
</template>
