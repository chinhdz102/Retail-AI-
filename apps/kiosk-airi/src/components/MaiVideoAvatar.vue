<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import {
  computeObjectFitCoverTransform,
  LipSyncAnalyzerState,
  sampleAudioElementLipSync,
  sampleTrackAnchor,
  VISEME_INDEX,
  type LipSyncFrame,
  type LipSyncTrackData,
} from '../lip-sync'
import type { AssistantStatus } from '../types'

type AvatarClipKey = 'idle' | 'listening' | 'thinking' | 'speaking'

const CLIP_FILES: ReadonlyArray<{ key: AvatarClipKey; suffix: string; label: string }> = [
  { key: 'idle', suffix: '01-idle', label: 'Mai đang chờ hỗ trợ' },
  { key: 'listening', suffix: '02-listening', label: 'Mai đang lắng nghe' },
  { key: 'thinking', suffix: '03-thinking', label: 'Mai đang tìm câu trả lời' },
  { key: 'speaking', suffix: '04-speaking', label: 'Mai đang nói chuyện' },
]

const CROSSFADE_MS = 220
const DEFAULT_BANDS = Object.freeze({ lowShare: 0.33, midShare: 0.5, highShare: 0.17 })

const props = withDefaults(defineProps<{
  state?: AssistantStatus
  audioPlaying?: boolean
  audioElement?: HTMLAudioElement | null
  speechSource?: 'none' | 'cloud' | 'browser'
  speechBoundaryToken?: number
  speechBoundaryWord?: string
  lipSyncEnabled?: boolean
  poster?: string
  videoBase?: string
  enabled?: boolean
  paused?: boolean
}>(), {
  state: 'idle',
  audioPlaying: undefined,
  audioElement: null,
  speechSource: 'none',
  speechBoundaryToken: 0,
  speechBoundaryWord: '',
  lipSyncEnabled: false,
  poster: '/avatars/mai-closed-mouth-reference.webp',
  videoBase: '/avatars/mai-clip-v4',
  enabled: true,
  paused: false,
})

const videoRefs = ref<Partial<Record<AvatarClipKey, HTMLVideoElement | null>>>({})
const lipCanvasRef = ref<HTMLCanvasElement | null>(null)
const failedClips = ref<Partial<Record<AvatarClipKey, boolean>>>({})
const reducedMotion = ref(false)
const lipSyncTrack = ref<LipSyncTrackData | null>(null)
const lipSyncSprite = ref<HTMLImageElement | null>(null)
const lipSyncFailed = ref(false)
const currentVisemeIndex = ref<number>(VISEME_INDEX.closed)

let pauseTimers: Partial<Record<AvatarClipKey, ReturnType<typeof setTimeout>>> = {}
let rafId: number | null = null
let lastRafTimestamp = 0
let maxObservedCloudEnergy = 0
let assetLoadToken = 0
const analyzer = new LipSyncAnalyzerState()

function setVideoRef(key: AvatarClipKey, el: unknown) {
  videoRefs.value[key] = el instanceof HTMLVideoElement ? el : null
}

const useLipSyncBase = computed(() =>
  props.enabled && props.lipSyncEnabled && !lipSyncFailed.value && !reducedMotion.value,
)

const isLipSyncAssetsReady = computed(() =>
  useLipSyncBase.value && lipSyncTrack.value !== null && lipSyncSprite.value !== null,
)

const desiredClipKey = computed<AvatarClipKey>(() => {
  if (props.state === 'speaking') {
    // Only move Mai's mouth while TTS audio is actually playing. During the brief
    // synthesis ticket fetch before audio begins, keep a warm attentive smile.
    return props.audioPlaying === false ? 'listening' : 'speaking'
  }
  if (props.state === 'listening') return 'listening'
  if (props.state === 'transcribing' || props.state === 'thinking' || props.state === 'error') {
    return 'thinking'
  }
  return 'idle'
})

const activeClipKey = computed<AvatarClipKey>(() => {
  const target = desiredClipKey.value
  if (!failedClips.value[target]) return target
  if (!failedClips.value.idle) return 'idle'
  return target
})

const hasPlayableVideo = computed(() =>
  props.enabled && !failedClips.value[activeClipKey.value],
)

const isLipSyncCanvasVisible = computed(() =>
  !props.paused && hasPlayableVideo.value && activeClipKey.value === 'speaking' && isLipSyncAssetsReady.value,
)

function clipSrc(key: AvatarClipKey, suffix: string): string {
  if (key === 'speaking' && useLipSyncBase.value) {
    return `${props.videoBase}-04-speaking-base.mp4`
  }
  return `${props.videoBase}-${suffix}.mp4`
}

function clearPauseTimer(key: AvatarClipKey) {
  const timer = pauseTimers[key]
  if (timer !== undefined) {
    clearTimeout(timer)
    delete pauseTimers[key]
  }
}

function configureMuted(element: HTMLVideoElement) {
  element.muted = true
  element.defaultMuted = true
  element.volume = 0
}

function playClip(key: AvatarClipKey) {
  if (!props.enabled || props.paused || reducedMotion.value || failedClips.value[key]) return
  const element = videoRefs.value[key]
  if (!element) return
  configureMuted(element)
  if (!element.paused && !element.ended) return
  void Promise.resolve(element.play?.()).catch(() => {
    // Autoplay can be blocked by a browser policy. The poster remains usable.
  })
}

function clearLipCanvas() {
  const canvas = lipCanvasRef.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  currentVisemeIndex.value = VISEME_INDEX.closed
}

function stopLipSyncLoop() {
  if (rafId !== null && typeof cancelAnimationFrame === 'function') {
    cancelAnimationFrame(rafId)
  }
  rafId = null
  lastRafTimestamp = 0
  analyzer.reset()
  clearLipCanvas()
}

function drawLipSyncFrame(frame: LipSyncFrame) {
  const canvas = lipCanvasRef.value
  const track = lipSyncTrack.value
  const sprite = lipSyncSprite.value
  const speakingVideo = videoRefs.value.speaking
  if (!canvas || !track || !sprite || !speakingVideo) return

  const cssWidth = canvas.clientWidth
  const cssHeight = canvas.clientHeight
  if (cssWidth <= 0 || cssHeight <= 0) return

  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const targetWidth = Math.max(1, Math.round(cssWidth * dpr))
  const targetHeight = Math.max(1, Math.round(cssHeight * dpr))
  if (canvas.width !== targetWidth || canvas.height !== targetHeight) {
    canvas.width = targetWidth
    canvas.height = targetHeight
  }

  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, cssWidth, cssHeight)

  currentVisemeIndex.value = frame.visemeIndex
  const hasPrimary = frame.visemeIndex > VISEME_INDEX.closed && frame.openness > 0.02
  const hasSecondary = frame.secondaryIndex > VISEME_INDEX.closed && frame.secondaryWeight > 0.02
  if (!hasPrimary && !hasSecondary) return

  const transform = computeObjectFitCoverTransform(
    cssWidth,
    cssHeight,
    track.videoWidth,
    track.videoHeight,
  )
  const [anchorX, anchorY] = sampleTrackAnchor(track, speakingVideo.currentTime || 0)
  const destX = transform.offsetX + anchorX * transform.scale
  const destY = transform.offsetY + anchorY * transform.scale
  const destW = track.patchWidth * transform.scale
  const destH = track.patchHeight * transform.scale
  const patchW = track.patchWidth
  const patchH = track.patchHeight

  ctx.imageSmoothingEnabled = true
  ctx.imageSmoothingQuality = 'high'

  if (hasSecondary) {
    ctx.globalAlpha = frame.secondaryWeight
    ctx.drawImage(
      sprite,
      frame.secondaryIndex * patchW,
      0,
      patchW,
      patchH,
      destX,
      destY,
      destW,
      destH,
    )
  }

  if (hasPrimary) {
    ctx.globalAlpha = frame.openness
    ctx.drawImage(
      sprite,
      frame.visemeIndex * patchW,
      0,
      patchW,
      patchH,
      destX,
      destY,
      destW,
      destH,
    )
  }
  ctx.globalAlpha = 1
}

function stepLipSyncTick(timestamp: number) {
  if (!isLipSyncCanvasVisible.value) {
    stopLipSyncLoop()
    return
  }

  const dtMs = lastRafTimestamp > 0 ? Math.min(80, Math.max(4, timestamp - lastRafTimestamp)) : 16.67
  lastRafTimestamp = timestamp

  let frame: LipSyncFrame | null = null
  const audio = props.audioElement
  const isPlayingNow = props.audioPlaying !== false

  if (props.speechSource === 'cloud' && audio) {
    if (isPlayingNow && !audio.paused && !audio.ended) {
      const sampled = sampleAudioElementLipSync(audio, analyzer, dtMs)
      if (sampled) {
        if (sampled.energy > maxObservedCloudEnergy) {
          maxObservedCloudEnergy = sampled.energy
        }
        // Guard against unexpected CORS-tainted zero-output environments so Mai never stays mute.
        if (audio.currentTime > 0.3 && maxObservedCloudEnergy < 0.0005) {
          frame = analyzer.stepBrowserFallback(true, dtMs)
        } else {
          frame = sampled
        }
      } else {
        frame = analyzer.stepBrowserFallback(true, dtMs)
      }
    } else {
      frame = analyzer.stepAudio(0, DEFAULT_BANDS, dtMs)
    }
  } else if (props.speechSource === 'browser') {
    frame = analyzer.stepBrowserFallback(isPlayingNow, dtMs)
  } else {
    frame = analyzer.stepBrowserFallback(isPlayingNow, dtMs)
  }

  if (frame) {
    drawLipSyncFrame(frame)
  }

  if (typeof requestAnimationFrame === 'function') {
    rafId = requestAnimationFrame(stepLipSyncTick)
  }
}

function startLipSyncLoop() {
  if (!isLipSyncCanvasVisible.value || rafId !== null || typeof requestAnimationFrame !== 'function') {
    return
  }
  lastRafTimestamp = 0
  rafId = requestAnimationFrame(stepLipSyncTick)
}

async function loadLipSyncAssets() {
  if (!props.enabled || !props.lipSyncEnabled || typeof fetch !== 'function' || typeof Image === 'undefined') {
    return
  }
  const token = ++assetLoadToken
  lipSyncFailed.value = false
  lipSyncTrack.value = null
  lipSyncSprite.value = null

  try {
    const trackUrl = `${props.videoBase}-04-speaking-track.json`
    const spriteUrl = `${props.videoBase}-04-speaking-visemes.png`
    const response = await fetch(trackUrl)
    if (!response.ok) throw new Error(`Track HTTP ${response.status}`)
    const parsed = (await response.json()) as LipSyncTrackData
    if (!parsed || !Array.isArray(parsed.anchors) || parsed.anchors.length === 0) {
      throw new Error('Invalid lip-sync track payload')
    }

    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const image = new Image()
      image.decoding = 'async'
      image.onload = () => resolve(image)
      image.onerror = () => reject(new Error('Failed to load viseme sprite'))
      image.src = spriteUrl
    })

    if (token !== assetLoadToken) return
    lipSyncTrack.value = parsed
    lipSyncSprite.value = img
    if (isLipSyncCanvasVisible.value) {
      startLipSyncLoop()
    }
  } catch {
    if (token !== assetLoadToken) return
    lipSyncFailed.value = true
    lipSyncTrack.value = null
    lipSyncSprite.value = null
    stopLipSyncLoop()
  }
}

function syncPlayback(previousKey?: AvatarClipKey) {
  if (!props.enabled) return
  if (props.paused) {
    stopLipSyncLoop()
    for (const { key } of CLIP_FILES) {
      clearPauseTimer(key)
      videoRefs.value[key]?.pause()
    }
    return
  }
  const targetKey = activeClipKey.value

  clearPauseTimer(targetKey)
  if (!reducedMotion.value) {
    playClip(targetKey)
  }

  for (const { key } of CLIP_FILES) {
    if (key === targetKey) continue
    const element = videoRefs.value[key]
    if (!element) continue
    configureMuted(element)
    clearPauseTimer(key)

    // Keep the outgoing clip moving briefly during the CSS opacity crossfade
    // so Mai never freezes mid-dissolve, then pause inactive layers to save GPU.
    if (key === previousKey && !reducedMotion.value) {
      pauseTimers[key] = setTimeout(() => {
        delete pauseTimers[key]
        if (activeClipKey.value !== key) {
          videoRefs.value[key]?.pause()
        }
      }, CROSSFADE_MS + 40)
    } else {
      element.pause()
    }
  }

  if (isLipSyncCanvasVisible.value) {
    startLipSyncLoop()
  } else {
    stopLipSyncLoop()
  }
}

function handleClipCanPlay(key: AvatarClipKey) {
  if (key === activeClipKey.value && !props.paused && !reducedMotion.value) {
    playClip(key)
  }
}

function handleClipError(key: AvatarClipKey) {
  if (key === 'speaking' && useLipSyncBase.value) {
    // Fall back from speaking-base.mp4 to the standalone lively 04-speaking.mp4 clip.
    lipSyncFailed.value = true
    stopLipSyncLoop()
    return
  }
  failedClips.value = { ...failedClips.value, [key]: true }
}

watch(
  () => [props.videoBase, props.enabled, props.lipSyncEnabled],
  async () => {
    failedClips.value = {}
    await nextTick()
    void loadLipSyncAssets()
    syncPlayback()
  },
  { flush: 'post' },
)

watch(
  activeClipKey,
  async (nextKey, prevKey) => {
    if (nextKey === prevKey) return
    if (nextKey === 'speaking') {
      maxObservedCloudEnergy = 0
      analyzer.reset()
    }
    await nextTick()
    syncPlayback(prevKey)
  },
  { flush: 'post' },
)

watch(
  () => props.audioPlaying,
  (playing) => {
    if (playing) {
      maxObservedCloudEnergy = 0
      if (isLipSyncCanvasVisible.value) {
        startLipSyncLoop()
      }
    }
  },
)

watch(
  () => props.paused,
  () => {
    syncPlayback()
  },
)

watch(
  () => props.speechBoundaryToken,
  (token, prevToken) => {
    if (token !== prevToken && token > 0 && props.speechSource === 'browser') {
      analyzer.pulseWordBoundary(props.speechBoundaryWord)
    }
  },
)

onMounted(() => {
  reducedMotion.value = window.matchMedia?.(
    '(prefers-reduced-motion: reduce)',
  ).matches ?? false
  for (const { key } of CLIP_FILES) {
    const element = videoRefs.value[key]
    if (element) configureMuted(element)
  }
  void loadLipSyncAssets()
  syncPlayback()
})

onUnmounted(() => {
  assetLoadToken += 1
  stopLipSyncLoop()
  for (const { key } of CLIP_FILES) {
    clearPauseTimer(key)
    videoRefs.value[key]?.pause()
  }
})
</script>

<template>
  <section
    class="sky-avatar sky-video-avatar"
    :class="[
      'is-' + state,
      {
        'is-fallback': !hasPlayableVideo,
        'is-audio-speaking': activeClipKey === 'speaking',
        'is-lipsync-live': isLipSyncCanvasVisible,
      },
    ]"
    :data-active-clip="activeClipKey"
    :data-lipsync-viseme="currentVisemeIndex"
    aria-label="Avatar Mai"
  >
    <div class="avatar-wrap">
      <template v-if="hasPlayableVideo">
        <video
          v-for="clip in CLIP_FILES"
          :key="`${videoBase}-${clip.key}-${clip.key === 'speaking' && useLipSyncBase ? 'base' : 'std'}`"
          :ref="(el) => setVideoRef(clip.key, el)"
          class="sky-video sky-video-layer"
          :class="{ 'is-active': activeClipKey === clip.key }"
          :data-clip="clip.key"
          :src="clipSrc(clip.key, clip.suffix)"
          :poster="clip.key === 'idle' ? poster : undefined"
          :muted="true"
          :default-muted="true"
          volume="0"
          loop
          playsinline
          preload="auto"
          :aria-label="clip.label"
          :aria-hidden="activeClipKey !== clip.key"
          @error="handleClipError(clip.key)"
          @canplay="handleClipCanPlay(clip.key)"
        ></video>
        <canvas
          ref="lipCanvasRef"
          class="sky-lipsync-canvas"
          :class="{ 'is-active': isLipSyncCanvasVisible }"
          aria-hidden="true"
        ></canvas>
      </template>
      <img
        v-else
        class="sky-video-poster"
        :src="poster"
        alt="Avatar Mai đang hỗ trợ mua sắm"
      />
    </div>
  </section>
</template>
