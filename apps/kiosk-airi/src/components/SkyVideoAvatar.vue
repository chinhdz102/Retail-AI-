<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import type { AssistantStatus } from '../types'

const props = withDefaults(defineProps<{
  state?: AssistantStatus
  poster?: string
  videoBase?: string
  enabled?: boolean
}>(), {
  state: 'idle',
  poster: '/avatars/sky-anime-reference.png',
  videoBase: '/avatars/mai-clip-v2',
  enabled: true,
})

const video = ref<HTMLVideoElement | null>(null)
const videoFailed = ref(false)
const reducedMotion = ref(false)

const clipForState: Record<AssistantStatus, string> = {
  // The supplied idle and listening clips contain occasional open-mouth
  // frames. Keep every silent state on the verified closed-mouth clip.
  idle: '03-thinking',
  listening: '03-thinking',
  transcribing: '03-thinking',
  thinking: '03-thinking',
  speaking: '04-speaking',
  error: '03-thinking',
}

const videoSource = computed(() => {
  if (!props.enabled || videoFailed.value) return null
  return `${props.videoBase}-${clipForState[props.state]}.mp4`
})

const isPlayingState = computed(() =>
  !reducedMotion.value && Boolean(videoSource.value),
)

function resetVideo() {
  const element = video.value
  if (!element || !videoSource.value) return

  element.muted = true
  element.defaultMuted = true
  element.volume = 0
  element.pause()
  element.currentTime = 0
  element.load()

  if (element.readyState >= 2) playVideo()
}

function playVideo() {
  const element = video.value
  if (!element || !isPlayingState.value) return
  void element.play().catch(() => {
    // Autoplay can be blocked by a browser policy. The poster remains usable.
  })
}

function handleVideoError() {
  videoFailed.value = true
}

watch(
  () => [props.state, props.enabled, props.videoBase],
  async () => {
    videoFailed.value = false
    await nextTick()
    resetVideo()
  },
  { flush: 'post' },
)

onMounted(() => {
  reducedMotion.value = window.matchMedia?.(
    '(prefers-reduced-motion: reduce)',
  ).matches ?? false
  resetVideo()
})
</script>

<template>
  <section
    class="sky-avatar sky-video-avatar"
    :class="['is-' + state, { 'is-fallback': !videoSource }]"
    aria-label="Avatar Mai"
  >
    <div class="avatar-wrap">
      <video
        v-if="videoSource"
        ref="video"
        class="sky-video"
        :src="videoSource"
        :poster="poster"
        :muted="true"
        :default-muted="true"
        volume="0"
        loop
        playsinline
        preload="metadata"
        aria-label="Mai đang cử động"
        @error="handleVideoError"
        @canplay="playVideo"
      ></video>
      <img
        v-else
        class="sky-video-poster"
        :src="poster"
        alt="Avatar Mai đang hỗ trợ mua sắm"
      />
    </div>
  </section>
</template>
