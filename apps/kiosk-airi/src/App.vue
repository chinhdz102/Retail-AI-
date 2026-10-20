<script setup lang="ts">
import {
  PhChatText as ChatText,
  PhMicrophone as Microphone,
  PhPause as Pause,
  PhSpeakerHigh as SpeakerHigh,
  PhStop as Stop,
  PhX as X,
} from '@phosphor-icons/vue'
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref } from 'vue'

import {
  addCartItem,
  cancelCheckout,
  closeSession,
  confirmCheckout,
  createRequestId,
  createSession,
  createSynthesisTicket,
  getCart,
  prepareCheckout,
  respond,
  setCartItemQuantity,
  simulateSandboxPayment,
  transcribe,
} from './api'
import ChatPanel from './components/ChatPanel.vue'
import type { CheckoutQuote, SandboxOrder, ShoppingCart } from './types'
const SkyAvatar = defineAsyncComponent(() => import('./components/SkyAvatar.vue'))
import { listMicrophoneOptions, preferredMicrophoneId } from './microphone-devices'
import type { AssistantStatus, ChatMessage, LanguageCode } from './types'
import {
  VoiceConversationController,
  type VoiceControllerError,
  type VoiceState,
} from './voice-controller'

const SILENT_AUDIO_DATA_URI =
  'data:audio/wav;base64,UklGRnQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YVAAAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgA=='
const sessionId = ref<string | null>(null)
const messages = ref<ChatMessage[]>([])
const cart = ref<ShoppingCart | null>(null)
const checkoutQuote = ref<CheckoutQuote | null>(null)
const sandboxOrder = ref<SandboxOrder | null>(null)
const draft = ref('')
const status = ref<AssistantStatus>('idle')
const errorMessage = ref<string | null>(null)
const isRecording = ref(false)
const isVoiceActive = ref(false)
const microphoneLevel = ref(0)
const microphoneDevices = ref<Array<{ id: string; label: string }>>([])
const selectedMicrophone = ref('')
const activeMicrophone = ref('')
const isBooting = ref(true)
const isHandsFree = ref(false)
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
let audioUnlockPromise: Promise<boolean> | null = null
let speechRequestId = 0
let voiceController: VoiceConversationController | null = null
let listeningStartedAt = 0
let sessionExpiryTimer: ReturnType<typeof setTimeout> | undefined
let sessionIdleTimeoutMs = 30 * 60 * 1000
let sessionGeneration = 0
let bootInFlight: { generation: number; promise: Promise<void> } | null = null
let resetInFlight: Promise<void> | null = null
let pendingTurn: { text: string; generation: number; id: string } | null = null
let activeTurnController: AbortController | null = null
let activeTurnFromVoice = false
let voiceTurnEpoch = 0

const languageSettings: Record<LanguageCode, {
  label: string
  locale: string
  switching: string
  ready: string
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
    ready: 'Nhập câu hỏi hoặc bấm mic để nói',
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
    ready: 'Type a question or tap the mic to speak.',
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
    ready: '输入问题，或点击麦克风开始说话。',
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
  if (language.value === 'en') return 'Click once to let Sky play audio.'
  if (language.value === 'zh') return '请点击一次，让 Sky 播放语音。'
  return 'Bấm một lần để cho phép Sky phát giọng nói.'
})

const textPanelLabel = computed(() => {
  if (language.value === 'en') return isTextOpen.value ? 'Close text' : 'Open text'
  if (language.value === 'zh') return isTextOpen.value ? '关闭文字' : '打开文字'
  return isTextOpen.value ? 'Đóng phần chữ' : 'Mở phần chữ'
})

const voiceButtonLabel = computed(() => {
  if (language.value === 'en') return isVoiceActive.value ? 'Stop listening' : 'Start speaking'
  if (language.value === 'zh') return isVoiceActive.value ? '停止聆听' : '开始说话'
  return isHandsFree.value ? 'Dừng nghe' : 'Bắt đầu nói'
})

const statusHint = computed(() => {
  if (status.value === 'listening') return language.value === 'en' ? 'Listening' : language.value === 'zh' ? '正在聆听' : 'Đang nghe'
  if (status.value === 'transcribing') return language.value === 'en' ? 'Recognizing' : language.value === 'zh' ? '正在识别' : 'Đang nhận diện'
  if (status.value === 'thinking') return language.value === 'en' ? 'Thinking' : language.value === 'zh' ? '正在思考' : 'Đang xử lý'
  if (status.value === 'speaking') return language.value === 'en' ? 'Speaking' : language.value === 'zh' ? '正在回答' : 'Đang trả lời'
  if (status.value === 'error') return language.value === 'en' ? 'Needs attention' : language.value === 'zh' ? '需要重试' : 'Cần thử lại'
  return isBooting.value ? (language.value === 'en' ? 'Starting' : language.value === 'zh' ? '正在启动' : 'Đang khởi tạo') : (language.value === 'en' ? 'Ready' : language.value === 'zh' ? '已准备好' : 'Sẵn sàng')
})

const microphoneLevelLabel = computed(() => language.value === 'en'
  ? 'Microphone signal level'
  : language.value === 'zh' ? '麦克风信号强度' : 'Mức tín hiệu micro')

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
  return messages.value[messages.value.length - 1]
}

function handleVoiceState(state: VoiceState) {
  isVoiceActive.value = state.mode !== 'idle'
  isRecording.value = state.phase === 'recording'
  if (state.phase === 'listening') {
    listeningStartedAt = Date.now()
    void refreshMicrophones()
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
  microphoneLevel.value = 0
  errorMessage.value = error.kind === 'permission'
    ? languageSettings[language.value].permission
    : error.message
  voiceHint.value = languageSettings[language.value].retry
  status.value = 'error'
}

async function handleVoiceAudio(audio: Blob) {
  if (!sessionId.value || isUnmounted || !isHandsFree.value) return
  const generation = sessionGeneration
  const voiceEpoch = voiceTurnEpoch
  status.value = 'transcribing'
  voiceHint.value = languageSettings[language.value].transcribing
  try {
    const result = await withSessionRecovery(
      (id) => transcribe(id, audio, language.value),
    )
    if (generation !== sessionGeneration || isUnmounted || voiceEpoch !== voiceTurnEpoch || !isHandsFree.value) return
    if (!result.text.trim()) throw new Error(
      language.value === 'vi'
        ? 'Không nghe rõ câu nói, bạn thử lại nhé'
        : language.value === 'en'
          ? 'I could not hear that clearly. Please try again.'
          : '没有听清楚，请再试一次。',
    )
    await sendMessage(result.text, true)
  } catch (error) {
    if (generation !== sessionGeneration || isUnmounted) return
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
    void resumeHandsFree()
  }
}

function ensureVoiceController(): VoiceConversationController {
  if (!voiceController) {
    voiceController = new VoiceConversationController({
      locale: languageSettings[language.value].locale,
      preferRecorder: true,
      validateSpeechEnvelope: true,
      deviceId: selectedMicrophone.value,
      onState: handleVoiceState,
      onTranscript: (text) => { void sendMessage(text, true) },
      onAudio: (audio) => { void handleVoiceAudio(audio) },
      onLevel: (level) => {
        microphoneLevel.value = level
        if (isVoiceActive.value && !isRecording.value && Date.now() - listeningStartedAt > 8_000) {
          voiceHint.value = language.value === 'en'
            ? 'I have not heard you yet. Move closer to the microphone or check the selected input.'
            : language.value === 'zh'
              ? '还没有听到你的声音。请靠近麦克风或检查所选输入设备。'
              : 'Chưa nhận được câu nói. Bạn nói gần mic hoặc kiểm tra micro đầu vào nhé.'
        }
      },
      onInputDevice: (label) => { activeMicrophone.value = label },
      onError: handleVoiceError,
    })
  }
  voiceController.setLocale(languageSettings[language.value].locale)
  return voiceController
}

async function refreshMicrophones() {
  try {
    const devices = await navigator.mediaDevices?.enumerateDevices()
    // Browsers hide physical labels until microphone permission is granted.
    // Wait for the first capture/listening event before choosing a device.
    if (!devices?.some((device) => device.kind === 'audioinput' && device.label.trim())) return
    const options = listMicrophoneOptions(devices ?? [])
    microphoneDevices.value = options
    let savedId = ''
    try {
      savedId = window.localStorage.getItem('retail-ai-microphone-device') ?? ''
    } catch {
      // Storage can be unavailable in private browsing; device selection still works in memory.
    }
    const preferredId = preferredMicrophoneId(options, selectedMicrophone.value, savedId)
    if (!preferredId) return

    const changed = selectedMicrophone.value !== preferredId
    selectedMicrophone.value = preferredId
    voiceController?.setInputDevice(preferredId)
    try {
      window.localStorage.setItem('retail-ai-microphone-device', preferredId)
    } catch {
      // Keep the selection for this page even when it cannot be persisted.
    }

    const selectedLabel = options.find((device) => device.id === preferredId)?.label ?? ''
    const normalizeDeviceLabel = (label: string) => label
      .replace(/^(?:default|communications)\s*-\s*/i, '')
      .trim()
      .toLocaleLowerCase()
    if (
      changed
      && isHandsFree.value
      && isVoiceActive.value
      && activeMicrophone.value
      && normalizeDeviceLabel(activeMicrophone.value) !== normalizeDeviceLabel(selectedLabel)
    ) {
      void changeMicrophone(preferredId)
    }
  } catch {
    // Capture can still work when the browser restricts device enumeration.
  }
}

async function changeMicrophone(deviceId: string) {
  const wasEnabled = isHandsFree.value
  pauseVoice()
  selectedMicrophone.value = deviceId
  voiceController?.setInputDevice(deviceId)
  try {
    window.localStorage.setItem('retail-ai-microphone-device', deviceId)
  } catch {
    // Keep the selection for this page even when it cannot be persisted.
  }
  activeMicrophone.value = ''
  if (wasEnabled) await startHandsFree()
}

async function startHandsFree() {
  if (isUnmounted) return
  if (!sessionId.value) await bootKiosk()
  if (!sessionId.value || isUnmounted) return
  // Voice turns need visible feedback: show listening state, transcript and any errors.
  isTextOpen.value = true
  unlockAudio()
  isHandsFree.value = true
  voiceHint.value = language.value === 'en'
    ? 'Opening the microphone. Allow microphone access if your browser asks.'
    : language.value === 'zh'
      ? '正在打开麦克风。如果浏览器询问，请允许使用麦克风。'
      : 'Đang mở micro. Hãy cho phép dùng micro nếu trình duyệt hỏi.'
  try {
    await ensureVoiceController().start()
  } catch (error) {
    handleVoiceError({ kind: 'runtime', message: toErrorMessage(error, 'Voice conversation could not start.'), cause: error })
  }
}

async function resumeHandsFree() {
  if (isHandsFree.value) await startHandsFree()
}

function pauseVoice() {
  voiceController?.pause()
  isVoiceActive.value = false
  isRecording.value = false
  microphoneLevel.value = 0
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
      if (!existingAudio && audio.src === SILENT_AUDIO_DATA_URI) {
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
  voiceHint.value = language.value === 'vi' ? 'Mình đang trả lời bằng giọng nói' : statusHint.value
  try {
    if (!sessionId.value || typeof Audio === 'undefined') {
      throw new Error('Cloud audio playback is unavailable')
    }
    const audioUrl = await withSessionRecovery((id) => createSynthesisTicket(id, text, language.value))
    if (requestId !== speechRequestId || isUnmounted) return
    pendingSpeechText.value = text
    const audio = audioElement.value ?? new Audio()
    if (!audioElement.value) audioElement.value = audio
    audio.preload = 'auto'
    audio.src = audioUrl
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
    void speak(pending, resumeHandsFree)
    return
  }
  void startHandsFree()
}

async function bootKiosk() {
  if (isUnmounted || sessionId.value) return
  const generation = sessionGeneration
  if (bootInFlight?.generation === generation) return bootInFlight.promise

  isBooting.value = true
  voiceHint.value = languageSettings[language.value].preparing
  const pending = (async () => {
    try {
      const session = await createSession('kiosk-01')
      if (isUnmounted || generation !== sessionGeneration) {
        try {
          await closeSession(session.session_id)
        } catch {
          // A stale boot must not leave a usable session behind when it can be closed.
        }
        return
      }
      sessionId.value = session.session_id
      const expiresAt = Date.parse(session.expires_at)
      if (Number.isFinite(expiresAt)) {
        sessionIdleTimeoutMs = Math.max(1_000, expiresAt - Date.now())
        scheduleSessionExpiry(session.expires_at)
      } else {
        sessionIdleTimeoutMs = 30 * 60 * 1000
      }
      try {
        const initialCart = await getCart(session.session_id)
        if (generation === sessionGeneration && sessionId.value === session.session_id) {
          cart.value = initialCart
        }
      } catch {
        // Catalog configuration can disable sandbox carts while text lookup is still available.
        if (generation === sessionGeneration && sessionId.value === session.session_id) {
          cart.value = null
        }
      }
      if (generation === sessionGeneration && sessionId.value === session.session_id) {
        voiceHint.value = languageSettings[language.value].ready
      }
    } catch (error) {
      if (generation === sessionGeneration && !isUnmounted) {
        errorMessage.value = toErrorMessage(error, 'Không mở được phiên kiosk')
        status.value = 'error'
        voiceHint.value = languageSettings[language.value].sessionError
      }
    } finally {
      if (generation === sessionGeneration) isBooting.value = false
    }
  })()
  bootInFlight = { generation, promise: pending }
  try {
    await pending
  } finally {
    if (bootInFlight?.promise === pending) bootInFlight = null
  }
}

function scheduleSessionExpiry(expiresAt: string) {
  if (sessionExpiryTimer) clearTimeout(sessionExpiryTimer)
  const expiryTimestamp = new Date(expiresAt).getTime()
  if (!Number.isFinite(expiryTimestamp)) return
  const delay = Math.max(0, expiryTimestamp - Date.now())
  sessionExpiryTimer = setTimeout(() => {
    void resetKioskSession('Phiên mua sắm đã hết hạn. Mình đã xóa nội dung và mở phiên mới.')
  }, delay)
}

async function resetKioskSession(notice?: string) {
  if (resetInFlight) return resetInFlight

  const generation = ++sessionGeneration
  voiceTurnEpoch += 1
  const oldSessionId = sessionId.value
  activeTurnController?.abort()
  activeTurnController = null
  activeTurnFromVoice = false
  if (sessionExpiryTimer) clearTimeout(sessionExpiryTimer)
  sessionExpiryTimer = undefined
  speechRequestId += 1
  pendingSpeechText.value = null
  pauseVoice()
  releaseAudioPlayback()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  sessionId.value = null
  messages.value = []
  pendingTurn = null
  draft.value = ''
  cart.value = null
  checkoutQuote.value = null
  sandboxOrder.value = null
  lastPrompt.value = ''
  errorMessage.value = null
  isHandsFree.value = false
  isRecording.value = false
  isVoiceActive.value = false
  status.value = 'idle'
  const pending = (async () => {
    if (oldSessionId) {
      try {
        await closeSession(oldSessionId)
      } catch {
        // The server may already have expired or closed this session.
      }
    }
    if (isUnmounted || generation !== sessionGeneration) return
    await bootKiosk()
    if (!isUnmounted && generation === sessionGeneration && notice) voiceHint.value = notice
  })()
  resetInFlight = pending
  try {
    await pending
  } finally {
    if (resetInFlight === pending) resetInFlight = null
  }
}

async function withSessionRecovery<T>(
  action: (id: string) => Promise<T>,
): Promise<T> {
  if (!sessionId.value) throw new Error('Chưa kết nối được phiên trò chuyện.')
  const activeSessionId = sessionId.value
  const generation = sessionGeneration
  const isCurrentSession = () => !isUnmounted
    && generation === sessionGeneration
    && sessionId.value === activeSessionId
  scheduleSessionExpiry(new Date(Date.now() + sessionIdleTimeoutMs).toISOString())
  try {
    const result = await action(activeSessionId)
    if (isCurrentSession()) {
      scheduleSessionExpiry(new Date(Date.now() + sessionIdleTimeoutMs).toISOString())
    }
    return result
  } catch (error) {
    if (!isCurrentSession()) throw error
    const failure = error as { status?: number; message?: string }
    const sessionGone = /session (?:is )?(?:closed|expired|not found)/i.test(failure?.message ?? '')
    if (![404, 409, 410].includes(failure?.status ?? 0) || !sessionGone) throw error
    await resetKioskSession(
      'Phiên trước đã kết thúc. Mình đã xóa hội thoại; bạn nói lại yêu cầu trong phiên mới nhé.',
    )
    throw new Error('Phiên trước đã kết thúc. Hội thoại cũ đã được xóa; vui lòng nói lại yêu cầu.')
  }
}

async function sendMessage(value = draft.value, fromVoice = false, isRetry = false) {
  const text = value.trim()
  const voiceEpoch = voiceTurnEpoch
  if (!text || status.value === 'thinking' || (status.value === 'transcribing' && !fromVoice)) return
  const callGeneration = sessionGeneration
  if (!sessionId.value) await bootKiosk()
  if (callGeneration !== sessionGeneration) return
  if (!sessionId.value || isUnmounted || (fromVoice && (!isHandsFree.value || voiceEpoch !== voiceTurnEpoch))) return
  const generation = sessionGeneration

  if (fromVoice) void unlockAudio()
  pauseVoice()
  draft.value = ''
  lastPrompt.value = text
  errorMessage.value = null
  if (!isRetry) pushMessage('user', text)
  const clientTurnId = isRetry
    && pendingTurn?.text === text
    && pendingTurn.generation === generation
    ? pendingTurn.id
    : createRequestId()
  pendingTurn = { text, generation, id: clientTurnId }
  const assistantMessage = pushMessage('assistant', '')
  const turnController = new AbortController()
  activeTurnController = turnController
  activeTurnFromVoice = fromVoice
  status.value = 'thinking'
  voiceHint.value = languageSettings[language.value].thinking

  try {
    let requestedSessionId: string | null = null
    const response = await withSessionRecovery((id) => {
      requestedSessionId = id
      return respond(
        id,
        text,
        language.value,
        clientTurnId,
        (delta) => {
          if (generation === sessionGeneration && !isUnmounted) {
            assistantMessage.text += delta
          }
        },
        turnController.signal,
      )
    })
    if (generation !== sessionGeneration || isUnmounted) return
    if (fromVoice && (voiceEpoch !== voiceTurnEpoch || !isHandsFree.value)) {
      const assistantIndex = messages.value.findIndex((message) => message.id === assistantMessage.id)
      if (assistantIndex >= 0) messages.value.splice(assistantIndex, 1)
      pendingTurn = null
      return
    }
    if (!requestedSessionId || response.session_id !== requestedSessionId) {
      throw new Error('Câu trả lời không thuộc phiên hội thoại hiện tại.')
    }
    if (response.state === 'CLOSED') {
      await resetKioskSession('Phiên đã kết thúc. Nội dung cũ đã được xóa khỏi kiosk.')
      return
    }
    assistantMessage.text = response.text
    assistantMessage.model = response.model
    assistantMessage.groundedProductIds = response.grounded_product_ids
    assistantMessage.products = response.products
    assistantMessage.supportTicket = response.support_ticket ?? undefined
    pendingTurn = null
    if (response.cart) cart.value = response.cart
    if (response.checkout_quote) checkoutQuote.value = response.checkout_quote
    if (response.order) sandboxOrder.value = response.order
    if (fromVoice) {
      void speak(response.text, resumeHandsFree)
    } else {
      status.value = 'idle'
      voiceHint.value = languageSettings[language.value].ready
      void resumeHandsFree()
    }
  } catch (error) {
    if (generation !== sessionGeneration || isUnmounted) return
    if (fromVoice && (voiceEpoch !== voiceTurnEpoch || !isHandsFree.value)) {
      const assistantIndex = messages.value.findIndex((message) => message.id === assistantMessage.id)
      if (assistantIndex >= 0) messages.value.splice(assistantIndex, 1)
      pendingTurn = null
      return
    }
    const assistantIndex = messages.value.findIndex((message) => message.id === assistantMessage.id)
    if (assistantIndex >= 0) messages.value.splice(assistantIndex, 1)
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
  } finally {
    if (activeTurnController === turnController) {
      activeTurnController = null
      activeTurnFromVoice = false
    }
  }
}

function handleQuickPrompt(value: string) {
  void sendMessage(value)
}

function showShoppingError(error: unknown) {
  errorMessage.value = toErrorMessage(error, 'Chưa cập nhật được giỏ demo. Bạn thử lại nhé.')
  status.value = 'error'
}

async function handleAddProduct(productId: string) {
  if (!sessionId.value) return
  const generation = sessionGeneration
  try {
    const updatedCart = await withSessionRecovery((id) => addCartItem(id, productId, 1, createRequestId()))
    if (generation !== sessionGeneration || isUnmounted) return
    cart.value = updatedCart
    checkoutQuote.value = null
    sandboxOrder.value = null
    status.value = 'idle'
    pushMessage('assistant', 'Mình đã thêm sản phẩm vào giỏ demo. Bạn có thể xem lại số lượng hoặc bắt đầu checkout.')
  } catch (error) {
    if (generation !== sessionGeneration || isUnmounted) return
    showShoppingError(error)
  }
}

async function handleSetCartQuantity(productId: string, quantity: number) {
  if (!sessionId.value) return
  const generation = sessionGeneration
  try {
    const updatedCart = await withSessionRecovery((id) => setCartItemQuantity(id, productId, quantity, createRequestId()))
    if (generation !== sessionGeneration || isUnmounted) return
    cart.value = updatedCart
    checkoutQuote.value = null
    sandboxOrder.value = null
  } catch (error) {
    if (generation !== sessionGeneration || isUnmounted) return
    showShoppingError(error)
  }
}

async function handlePrepareCheckout() {
  if (!sessionId.value) return
  const generation = sessionGeneration
  try {
    const quote = await withSessionRecovery((id) => prepareCheckout(id))
    if (generation !== sessionGeneration || isUnmounted) return
    checkoutQuote.value = quote
    cart.value = quote.cart
    sandboxOrder.value = null
  } catch (error) {
    if (generation !== sessionGeneration || isUnmounted) return
    showShoppingError(error)
  }
}

async function handleConfirmCheckout() {
  if (!sessionId.value || !checkoutQuote.value) return
  const generation = sessionGeneration
  try {
    const quote = checkoutQuote.value
    sandboxOrder.value = await withSessionRecovery((id) =>
      confirmCheckout(id, quote.confirmation_token, createRequestId()),
    )
    if (generation !== sessionGeneration || isUnmounted) return
    checkoutQuote.value = null
  } catch (error) {
    if (generation !== sessionGeneration || isUnmounted) return
    showShoppingError(error)
  }
}

async function handleCancelCheckout() {
  if (!sessionId.value) return
  const generation = sessionGeneration
  try {
    const updatedCart = await withSessionRecovery((id) => cancelCheckout(id))
    if (generation !== sessionGeneration || isUnmounted) return
    cart.value = updatedCart
    checkoutQuote.value = null
  } catch (error) {
    if (generation !== sessionGeneration || isUnmounted) return
    showShoppingError(error)
  }
}

async function handleSandboxPayment(outcome: 'PAID' | 'FAILED') {
  if (!sessionId.value || !sandboxOrder.value) return
  const generation = sessionGeneration
  try {
    const order = await withSessionRecovery((id) =>
      simulateSandboxPayment(id, sandboxOrder.value!.order_id, outcome, createRequestId()),
    )
    if (generation !== sessionGeneration || isUnmounted) return
    if (order.payment_status === 'PAID') {
      await resetKioskSession('Thanh toán mô phỏng hoàn tất; không phát sinh giao dịch thật. Kiosk đã mở phiên mới.')
      return
    }
    sandboxOrder.value = order
    const updatedCart = await withSessionRecovery((id) => getCart(id))
    if (generation !== sessionGeneration || isUnmounted) return
    cart.value = updatedCart
  } catch (error) {
    if (generation !== sessionGeneration || isUnmounted) return
    showShoppingError(error)
  }
}

function clearSandboxOrder() {
  sandboxOrder.value = null
}

function handleEndSession() {
  void resetKioskSession('Phiên đã kết thúc. Nội dung cũ đã được xóa khỏi kiosk.')
}

function handleRequestSupport() {
  void sendMessage(
    language.value === 'en'
      ? "I'd like to speak to a staff member."
      : language.value === 'zh'
        ? '我想联系店员。'
        : 'Mình muốn gặp nhân viên.',
  )
}

function toggleTextPanel() {
  isTextOpen.value = !isTextOpen.value
}

function toggleRecording() {
  unlockAudio()
  if (isHandsFree.value || voiceController?.isActive || isVoiceActive.value) {
    voiceTurnEpoch += 1
    if (activeTurnFromVoice) activeTurnController?.abort()
    isHandsFree.value = false
    speechRequestId += 1
    pendingSpeechText.value = null
    releaseAudioPlayback()
    if ('speechSynthesis' in window) window.speechSynthesis.cancel()
    // The stop button means discard, not 'submit a manual short utterance'.
    voiceController?.pause()
    isVoiceActive.value = false
    isRecording.value = false
    voiceHint.value = languageSettings[language.value].ready
    status.value = 'idle'
    return
  }
  errorMessage.value = null
  void startHandsFree()
}

function stopSpeaking() {
  speechRequestId += 1
  pendingSpeechText.value = null
  releaseAudioPlayback()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  status.value = 'idle'
  voiceHint.value = languageSettings[language.value].listening
  void resumeHandsFree()
}

function retryLastPrompt() {
  const prompt = lastPrompt.value
  errorMessage.value = null
  status.value = 'idle'
  if (prompt) void sendMessage(prompt, false, true)
  else void startHandsFree()
}

function handleLanguageChange(nextLanguage: LanguageCode) {
  if (language.value === nextLanguage) return
  voiceTurnEpoch += 1
  if (activeTurnFromVoice) activeTurnController?.abort()
  language.value = nextLanguage
  document.documentElement.lang = nextLanguage === 'en' ? 'en-US' : nextLanguage === 'zh' ? 'zh-CN' : 'vi-VN'
  voiceHint.value = languageSettings[nextLanguage].switching
  errorMessage.value = null
  const wasVoiceActive = isVoiceActive.value
  pauseVoice()
  voiceController?.setLocale(languageSettings[nextLanguage].locale)
  const canResumeListening = !['thinking', 'transcribing', 'speaking'].includes(status.value)
  if (sessionId.value && (wasVoiceActive || isHandsFree.value) && canResumeListening) void startHandsFree()
}

onMounted(() => {
  void bootKiosk()
  void refreshMicrophones()
  navigator.mediaDevices?.addEventListener?.('devicechange', refreshMicrophones)
})
onUnmounted(() => {
  isUnmounted = true
  voiceTurnEpoch += 1
  activeTurnController?.abort()
  activeTurnController = null
  const closingSessionId = sessionId.value
  if (sessionExpiryTimer) clearTimeout(sessionExpiryTimer)
  navigator.mediaDevices?.removeEventListener?.('devicechange', refreshMicrophones)
  voiceController?.stop()
  speechRequestId += 1
  releaseAudioPlayback()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  if (closingSessionId) void closeSession(closingSessionId).catch(() => {
    // The browser may be navigating away; backend TTL still bounds any session that cannot close.
  })
})
</script>

<template>
  <main class="vrm-only-shell" :class="{ 'text-is-open': isTextOpen }">
    <section class="vrm-stage" :aria-label="language === 'en' ? 'Sky avatar' : language === 'zh' ? 'Sky 虚拟形象' : 'Avatar Sky'">
      <SkyAvatar src="/avatars/sky.glb" :state="status" />

      <div class="voice-feedback" role="status" aria-live="polite">
        <strong>{{ statusHint }}</strong>
        <p>{{ errorMessage || voiceHint }}</p>
        <meter v-if="isVoiceActive" class="microphone-level" min="0" max="1" :value="microphoneLevel" :aria-label="microphoneLevelLabel"></meter>
      </div>

      <div class="vrm-controls" :aria-label="language === 'en' ? 'Sky controls' : language === 'zh' ? 'Sky 控制' : 'Điều khiển Sky'">
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
          <span>{{ voiceButtonLabel }}</span>
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
      <aside v-if="isTextOpen" class="text-drawer" :aria-label="language === 'en' ? 'Text conversation' : language === 'zh' ? '文字对话' : 'Bảng hội thoại dạng chữ'">
        <button
          type="button"
          class="drawer-close"
          :aria-label="language === 'en' ? 'Close text panel' : language === 'zh' ? '关闭文字面板' : 'Đóng phần chữ'"
          @click="isTextOpen = false"
        >
          <X :size="20" weight="bold" aria-hidden="true" />
        </button>
        <ChatPanel
          :messages="messages"
          :cart="cart"
          :checkout-quote="checkoutQuote"
          :order="sandboxOrder"
          :status="status"
          :draft="draft"
          :is-recording="isRecording"
          :is-voice-active="isVoiceActive"
          :is-hands-free="isHandsFree"
          :language="language"
          :voice-hint="voiceHint"
          :error-message="errorMessage"
          :microphone-level="microphoneLevel"
          :microphone-devices="microphoneDevices"
          :selected-microphone="selectedMicrophone"
          :active-microphone="activeMicrophone"
          @update:draft="draft = $event"
          @send="sendMessage()"
          @quick-prompt="handleQuickPrompt"
          @toggle-recording="toggleRecording"
          @stop-speaking="stopSpeaking"
          @change-language="handleLanguageChange"
          @change-microphone="changeMicrophone"
          @retry="retryLastPrompt"
          @add-product="handleAddProduct"
          @set-cart-quantity="handleSetCartQuantity"
          @prepare-checkout="handlePrepareCheckout"
          @confirm-checkout="handleConfirmCheckout"
          @cancel-checkout="handleCancelCheckout"
          @simulate-payment="handleSandboxPayment"
          @clear-order="clearSandboxOrder"
          @end-session="handleEndSession"
          @request-support="handleRequestSupport"
        />
      </aside>
    </Transition>

    <audio ref="audioElement" class="sr-only" preload="auto" aria-hidden="true"></audio>
  </main>
</template>
