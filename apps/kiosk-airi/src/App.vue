<script setup lang="ts">
import {
  PhChatText as ChatText,
  PhMicrophone as Microphone,
  PhPause as Pause,
  PhSpeakerHigh as SpeakerHigh,
  PhStop as Stop,
  PhX as X,
} from '@phosphor-icons/vue'
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { createSession, respond, synthesize, transcribe } from './api'
import ChatPanel from './components/ChatPanel.vue'
import SkyAvatar from './components/SkyAvatar.vue'
import type { AssistantStatus, ChatMessage, LanguageCode } from './types'
import {
  VoiceConversationController,
  type VoiceControllerError,
  type VoiceState,
} from './voice-controller'

const WELCOME_MESSAGE =
  'Chào bạn, mình là Sky. Bạn cứ nói điều mình có thể giúp trong cửa hàng nhé.'
const SILENT_AUDIO_DATA_URI =
  'data:audio/wav;base64,UklGRnQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YVAAAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgA=='
const sessionId = ref<string | null>(null)
const messages = ref<ChatMessage[]>([])
const draft = ref('')
const status = ref<AssistantStatus>('idle')
const errorMessage = ref<string | null>(null)
const isRecording = ref(false)
const isVoiceActive = ref(false)
const isBooting = ref(true)
const isHandsFree = ref(true)
const isTextOpen = ref(false)
const language = ref<LanguageCode>('vi')
const voiceHint = ref('Đang chuẩn bị chế độ hội thoại tiếng Việt')
const lastPrompt = ref('')
const audioElement = ref<HTMLAudioElement | null>(null)
const audioUnlocked = ref(false)
const needsAudioUnlock = ref(false)
const pendingSpeechText = ref<string | null>(null)

let isUnmounted = false
let audioPlayback: HTMLAudioElement | null = null
let audioObjectUrl: string | null = null
let audioUnlockPromise: Promise<boolean> | null = null
let speechRequestId = 0
let voiceController: VoiceConversationController | null = null

const languageSettings: Record<LanguageCode, {
  label: string
  locale: string
  switching: string
  listening: string
  preparing: string
  thinking: string
  recording: string
  transcribing: string
  permission: string
  retry: string
  sessionError: string
}> = {
  vi: {
    label: 'tiếng Việt',
    locale: 'vi-VN',
    switching: 'Đang chuyển sang tiếng Việt',
    listening: 'Mình đang nghe, bạn cứ nói tự nhiên nhé',
    preparing: 'Đang chuẩn bị chế độ hội thoại tiếng Việt',
    thinking: 'Mình đang tìm câu trả lời cho bạn',
    recording: 'Đang ghi âm tiếng Việt',
    transcribing: 'Đang nhận diện tiếng Việt',
    permission: 'Microphone chưa được cấp quyền. Bạn có thể nhập câu hỏi bằng bàn phím.',
    retry: 'Mình sẽ nghe lại sau một chút',
    sessionError: 'Không kết nối được phiên trò chuyện. Bạn có thể thử lại.',
  },
  en: {
    label: 'English',
    locale: 'en-US',
    switching: 'Switching to English',
    listening: "I'm listening. Speak naturally.",
    preparing: 'Preparing English conversation mode',
    thinking: 'Finding the right answer for you',
    recording: 'Recording English speech',
    transcribing: 'Recognizing English speech',
    permission: 'Microphone access is blocked. You can type your question instead.',
    retry: "I'll listen again in a moment",
    sessionError: 'Could not connect to the conversation session. Please try again.',
  },
  zh: {
    label: '中文',
    locale: 'zh-CN',
    switching: '正在切换到中文',
    listening: '我在听，请自然地说话。',
    preparing: '正在准备中文对话模式',
    thinking: '正在为你查找答案',
    recording: '正在录制中文语音',
    transcribing: '正在识别中文语音',
    permission: '麦克风尚未授权。你也可以直接输入问题。',
    retry: '我稍后会再次聆听',
    sessionError: '无法连接对话会话，请重试。',
  },
}

const audioUnlockLabel = computed(() => {
  if (language.value === 'en') return 'Enable audio'
  if (language.value === 'zh') return '开启声音'
  return 'Bật âm thanh'
})

const audioUnlockHint = computed(() => {
  if (language.value === 'en') return 'Click once to let Sky play the ElevenLabs voice.'
  if (language.value === 'zh') return '请点击一次，让 Sky 播放 ElevenLabs 语音。'
  return 'Bấm một lần để cho phép Sky phát giọng ElevenLabs.'
})

const textPanelLabel = computed(() => {
  if (language.value === 'en') return isTextOpen.value ? 'Close text' : 'Open text'
  if (language.value === 'zh') return isTextOpen.value ? '关闭文字' : '打开文字'
  return isTextOpen.value ? 'Đóng phần chữ' : 'Mở phần chữ'
})

const voiceButtonLabel = computed(() => {
  if (language.value === 'en') return isVoiceActive.value ? 'Stop listening' : 'Start speaking'
  if (language.value === 'zh') return isVoiceActive.value ? '停止聆听' : '开始说话'
  return isVoiceActive.value ? 'Dừng nghe' : 'Bắt đầu nói'
})

const statusHint = computed(() => {
  if (status.value === 'listening') return language.value === 'en' ? 'Listening' : language.value === 'zh' ? '正在聆听' : 'Đang nghe'
  if (status.value === 'transcribing') return language.value === 'en' ? 'Recognizing' : language.value === 'zh' ? '正在识别' : 'Đang nhận diện'
  if (status.value === 'thinking') return language.value === 'en' ? 'Thinking' : language.value === 'zh' ? '正在思考' : 'Đang xử lý'
  if (status.value === 'speaking') return language.value === 'en' ? 'Speaking' : language.value === 'zh' ? '正在回答' : 'Đang trả lời'
  if (status.value === 'error') return language.value === 'en' ? 'Needs attention' : language.value === 'zh' ? '需要重试' : 'Cần thử lại'
  return isBooting.value ? (language.value === 'en' ? 'Starting' : language.value === 'zh' ? '正在启动' : 'Đang khởi tạo') : (language.value === 'en' ? 'Ready' : language.value === 'zh' ? '已准备好' : 'Sẵn sàng')
})

function toErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

function pushMessage(role: ChatMessage['role'], text: string, metadata?: Partial<ChatMessage>) {
  messages.value.push({
    id: `${role}-${Date.now()}-${messages.value.length}`,
    role,
    text,
    ...metadata,
  })
}

function handleVoiceState(state: VoiceState) {
  isVoiceActive.value = state.mode !== 'idle'
  isHandsFree.value = state.mode !== 'idle'
  isRecording.value = state.phase === 'recording'
  if (state.phase === 'listening') {
    status.value = 'listening'
    voiceHint.value = languageSettings[language.value].listening
    return
  }
  if (state.phase === 'recording') {
    status.value = 'listening'
    voiceHint.value = languageSettings[language.value].recording
    return
  }
  if (state.mode === 'idle' && !['thinking', 'transcribing', 'speaking', 'error'].includes(status.value)) {
    status.value = 'idle'
  }
}

function handleVoiceError(error: VoiceControllerError) {
  isVoiceActive.value = false
  isHandsFree.value = false
  isRecording.value = false
  errorMessage.value = error.kind === 'permission'
    ? languageSettings[language.value].permission
    : error.message
  voiceHint.value = languageSettings[language.value].retry
  status.value = 'error'
}

async function handleVoiceAudio(audio: Blob) {
  if (!sessionId.value || isUnmounted) return
  status.value = 'transcribing'
  voiceHint.value = languageSettings[language.value].transcribing
  try {
    const result = await transcribe(sessionId.value, audio, language.value)
    if (!result.text.trim()) throw new Error(
      language.value === 'vi'
        ? 'Không nghe rõ câu nói, bạn thử lại nhé'
        : language.value === 'en'
          ? 'I could not hear that clearly. Please try again.'
          : '没有听清楚，请再试一次。',
    )
    await sendMessage(result.text, true)
  } catch (error) {
    errorMessage.value = toErrorMessage(
      error,
      language.value === 'vi'
        ? 'Không nhận diện được tiếng nói'
        : language.value === 'en'
          ? 'I could not recognize that speech.'
          : '无法识别这段语音。',
    )
    status.value = 'error'
    voiceHint.value = languageSettings[language.value].retry
    void startHandsFree()
  }
}

function ensureVoiceController(): VoiceConversationController {
  if (!voiceController) {
    voiceController = new VoiceConversationController({
      locale: languageSettings[language.value].locale,
      onState: handleVoiceState,
      onTranscript: (text) => { void sendMessage(text) },
      onAudio: (audio) => { void handleVoiceAudio(audio) },
      onError: handleVoiceError,
    })
  }
  voiceController.setLocale(languageSettings[language.value].locale)
  return voiceController
}

async function startHandsFree() {
  if (!sessionId.value || isUnmounted) return
  unlockAudio()
  isHandsFree.value = true
  errorMessage.value = null
  try {
    await ensureVoiceController().start()
  } catch (error) {
    handleVoiceError({ kind: 'runtime', message: toErrorMessage(error, 'Voice conversation could not start.'), cause: error })
  }
}

function pauseVoice() {
  voiceController?.pause()
  isVoiceActive.value = false
  isRecording.value = false
}

function unlockAudio(): Promise<boolean> {
  if ('speechSynthesis' in window) window.speechSynthesis.resume?.()
  if (audioUnlocked.value) return Promise.resolve(true)
  if (audioUnlockPromise) return audioUnlockPromise

  const existingAudio = audioPlayback
  const audio = existingAudio ?? audioElement.value ?? (typeof Audio === 'undefined' ? null : new Audio())
  if (!audio) return Promise.resolve(false)
  if (!audioElement.value) audioElement.value = audio

  if (!existingAudio) {
    audio.src = SILENT_AUDIO_DATA_URI
    audio.preload = 'auto'
  }

  let playPromise: Promise<void>
  try {
    playPromise = audio.play()
  } catch {
    return Promise.resolve(false)
  }

  audioUnlockPromise = Promise.resolve(playPromise)
    .then(() => {
      audioUnlocked.value = true
      needsAudioUnlock.value = false
      if (!existingAudio) {
        audio.pause()
        audio.removeAttribute('src')
        try {
          audio.currentTime = 0
        } catch {
          // Some test/browser implementations do not allow seeking before metadata loads.
        }
      }
      audioUnlockPromise = null
      return true
    })
    .catch(() => {
      audioUnlockPromise = null
      return false
    })
  return audioUnlockPromise
}

function isAutoplayBlocked(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false
  return 'name' in error && (error as { name?: string }).name === 'NotAllowedError'
}

function releaseAudioPlayback() {
  if (audioPlayback) {
    audioPlayback.onplay = null
    audioPlayback.onended = null
    audioPlayback.onerror = null
    audioPlayback.pause()
    audioPlayback.removeAttribute('src')
    audioPlayback = null
  }
  if (audioObjectUrl) {
    URL.revokeObjectURL(audioObjectUrl)
    audioObjectUrl = null
  }
}

function speakWithBrowser(text: string, afterSpeech?: () => void, requestId = speechRequestId): boolean {
  if (!('speechSynthesis' in window) || typeof SpeechSynthesisUtterance === 'undefined') {
    status.value = 'idle'
    afterSpeech?.()
    return false
  }

  const synthesis = window.speechSynthesis
  synthesis.cancel()
  synthesis.resume?.()
  pauseVoice()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = languageSettings[language.value].locale
  utterance.rate = 0.88
  utterance.pitch = 1.02
  const preferredLanguage = languageSettings[language.value].locale.slice(0, 2).toLowerCase()
  const preferredVoice = synthesis.getVoices().find((voice) => voice.lang.toLowerCase().startsWith(preferredLanguage))
  if (preferredVoice) utterance.voice = preferredVoice
  utterance.onstart = () => { status.value = 'speaking' }
  const finish = () => {
    if (requestId !== speechRequestId || isUnmounted) return
    status.value = 'idle'
    afterSpeech?.()
  }
  utterance.onend = finish
  utterance.onerror = finish
  status.value = 'speaking'
  synthesis.speak(utterance)
  return true
}

async function speak(text: string, afterSpeech?: () => void) {
  const requestId = ++speechRequestId
  pauseVoice()
  releaseAudioPlayback()
  status.value = 'speaking'
  try {
    if (!sessionId.value || typeof Audio === 'undefined' || typeof URL.createObjectURL !== 'function') {
      throw new Error('ElevenLabs audio playback is unavailable')
    }
    const blob = await synthesize(sessionId.value, text, language.value)
    if (requestId !== speechRequestId || isUnmounted) return
    audioObjectUrl = URL.createObjectURL(blob)
    pendingSpeechText.value = text
    const audio = audioElement.value ?? new Audio()
    if (!audioElement.value) audioElement.value = audio
    audio.src = audioObjectUrl
    audioPlayback = audio
    audio.onplay = () => {
      if (requestId !== speechRequestId || isUnmounted) return
      audioUnlocked.value = true
      needsAudioUnlock.value = false
      status.value = 'speaking'
    }
    audio.onended = () => {
      if (requestId !== speechRequestId || isUnmounted) return
      releaseAudioPlayback()
      pendingSpeechText.value = null
      status.value = 'idle'
      afterSpeech?.()
    }
    audio.onerror = () => {
      if (requestId !== speechRequestId || isUnmounted) return
      pendingSpeechText.value = null
      releaseAudioPlayback()
      speakWithBrowser(text, afterSpeech, requestId)
    }
    try {
      await audio.play()
    } catch (error) {
      if (isAutoplayBlocked(error)) {
        needsAudioUnlock.value = true
        status.value = 'idle'
        voiceHint.value = audioUnlockHint.value
        return
      }
      throw error
    }
  } catch {
    if (requestId !== speechRequestId || isUnmounted) return
    releaseAudioPlayback()
    pendingSpeechText.value = null
    speakWithBrowser(text, afterSpeech, requestId)
  }
}

async function activateAudio() {
  const unlocked = await unlockAudio()
  if (!unlocked) {
    errorMessage.value = audioUnlockHint.value
    return
  }
  if (!audioPlayback && pendingSpeechText.value) {
    const pending = pendingSpeechText.value
    void speak(pending, startHandsFree)
    return
  }
  void startHandsFree()
}

async function bootKiosk() {
  isBooting.value = true
  voiceHint.value = languageSettings[language.value].preparing
  try {
    const session = await createSession('kiosk-01')
    sessionId.value = session.session_id
    pushMessage('assistant', WELCOME_MESSAGE)
    void speak(WELCOME_MESSAGE, startHandsFree)
  } catch (error) {
    errorMessage.value = toErrorMessage(error, 'Không mở được phiên kiosk')
    status.value = 'error'
    voiceHint.value = languageSettings[language.value].sessionError
  } finally {
    isBooting.value = false
  }
}

async function sendMessage(value = draft.value, fromVoice = false) {
  const text = value.trim()
  if (!text || !sessionId.value || status.value === 'thinking' || (status.value === 'transcribing' && !fromVoice)) return

  void unlockAudio()
  pauseVoice()
  draft.value = ''
  lastPrompt.value = text
  errorMessage.value = null
  pushMessage('user', text)
  status.value = 'thinking'
  voiceHint.value = languageSettings[language.value].thinking

  try {
    const response = await respond(sessionId.value, text, language.value)
    pushMessage('assistant', response.text, {
      model: response.model,
      groundedProductIds: response.grounded_product_ids,
    })
    void speak(response.text, startHandsFree)
  } catch (error) {
    errorMessage.value = toErrorMessage(
      error,
      language.value === 'vi'
        ? 'Không nhận được câu trả lời từ trợ lý'
        : language.value === 'en'
          ? 'The assistant did not return an answer.'
          : '助手没有返回答案。',
    )
    voiceHint.value = language.value === 'vi'
      ? 'Bạn thử lại câu hỏi vừa rồi nhé'
      : language.value === 'en'
        ? 'Please try that question again.'
        : '请再试一次刚才的问题。'
    status.value = 'error'
  }
}

function handleQuickPrompt(value: string) {
  void sendMessage(value)
}

function toggleTextPanel() {
  isTextOpen.value = !isTextOpen.value
}

function toggleRecording() {
  unlockAudio()
  if (voiceController?.isActive || isVoiceActive.value) {
    voiceController?.stopCapture()
    isHandsFree.value = false
    isVoiceActive.value = false
    isRecording.value = false
    voiceHint.value = languageSettings[language.value].retry
    status.value = 'idle'
    return
  }
  void startHandsFree()
}

function stopSpeaking() {
  speechRequestId += 1
  pendingSpeechText.value = null
  releaseAudioPlayback()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  status.value = 'idle'
  voiceHint.value = languageSettings[language.value].listening
  void startHandsFree()
}

function retryLastPrompt() {
  const prompt = lastPrompt.value
  errorMessage.value = null
  status.value = 'idle'
  if (prompt) void sendMessage(prompt)
  else void startHandsFree()
}

function handleLanguageChange(nextLanguage: LanguageCode) {
  if (language.value === nextLanguage) return
  language.value = nextLanguage
  voiceHint.value = languageSettings[nextLanguage].switching
  errorMessage.value = null
  const wasVoiceActive = isVoiceActive.value
  pauseVoice()
  voiceController?.setLocale(languageSettings[nextLanguage].locale)
  const canResumeListening = !['thinking', 'transcribing', 'speaking'].includes(status.value)
  if (sessionId.value && (wasVoiceActive || isHandsFree.value) && canResumeListening) void startHandsFree()
}

onMounted(() => { void bootKiosk() })
onUnmounted(() => {
  isUnmounted = true
  voiceController?.stop()
  speechRequestId += 1
  releaseAudioPlayback()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
})
</script>

<template>
  <main class="vrm-only-shell" :class="{ 'text-is-open': isTextOpen }">
    <section class="vrm-stage" aria-label="Avatar Sky">
      <SkyAvatar src="/avatars/sky.glb" :state="status" />

      <div class="sr-only" role="status" aria-live="polite">{{ statusHint }}</div>

      <div class="vrm-controls" aria-label="Điều khiển Sky">
        <button
          v-if="needsAudioUnlock"
          type="button"
          class="vrm-control vrm-audio-control"
          :aria-label="audioUnlockLabel"
          :title="audioUnlockHint"
          @click="activateAudio"
        >
          <SpeakerHigh :size="22" weight="duotone" aria-hidden="true" />
        </button>
        <button
          type="button"
          class="vrm-control vrm-mic-control"
          :class="{ active: isVoiceActive, recording: isRecording }"
          :aria-label="voiceButtonLabel"
          :disabled="status === 'transcribing' || status === 'thinking'"
          @click="toggleRecording"
        >
          <Stop v-if="isVoiceActive" :size="22" weight="fill" aria-hidden="true" />
          <Microphone v-else :size="22" weight="duotone" aria-hidden="true" />
        </button>
        <button
          v-if="status === 'speaking'"
          type="button"
          class="vrm-control"
          :aria-label="language === 'en' ? 'Stop Sky speaking' : language === 'zh' ? '停止 Sky 说话' : 'Dừng Sky nói'"
          @click="stopSpeaking"
        >
          <Pause :size="21" weight="fill" aria-hidden="true" />
        </button>
        <button
          type="button"
          class="vrm-control vrm-text-control"
          :aria-label="textPanelLabel"
          :aria-expanded="isTextOpen"
          @click="toggleTextPanel"
        >
          <X v-if="isTextOpen" :size="22" weight="bold" aria-hidden="true" />
          <ChatText v-else :size="22" weight="duotone" aria-hidden="true" />
        </button>
      </div>
    </section>

    <Transition name="text-drawer">
      <aside v-if="isTextOpen" class="text-drawer" aria-label="Bảng hội thoại dạng chữ">
        <button
          type="button"
          class="drawer-close"
          aria-label="Đóng phần chữ"
          @click="isTextOpen = false"
        >
          <X :size="20" weight="bold" aria-hidden="true" />
        </button>
        <ChatPanel
          :messages="messages"
          :status="status"
          :draft="draft"
          :is-recording="isRecording"
          :is-voice-active="isVoiceActive"
          :is-hands-free="isHandsFree"
          :language="language"
          :voice-hint="voiceHint"
          :error-message="errorMessage"
          @update:draft="draft = $event"
          @send="sendMessage()"
          @quick-prompt="handleQuickPrompt"
          @toggle-recording="toggleRecording"
          @stop-speaking="stopSpeaking"
          @change-language="handleLanguageChange"
          @retry="retryLastPrompt"
        />
      </aside>
    </Transition>

    <audio ref="audioElement" class="sr-only" preload="auto" aria-hidden="true"></audio>
  </main>
</template>
