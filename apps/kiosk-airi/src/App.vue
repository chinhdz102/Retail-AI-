<script setup lang="ts">
import {
  PhBellRinging as BellRinging,
  PhCamera as Camera,
  PhChatText as ChatText,
  PhMicrophone as Microphone,
  PhPause as Pause,
  PhShoppingBagOpen as ShoppingBagOpen,
  PhSpeakerHigh as SpeakerHigh,
  PhSquaresFour as SquaresFour,
  PhStop as Stop,
  PhX as X,
} from '@phosphor-icons/vue'
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref, shallowReactive } from 'vue'
import { LocalVoiceDiagnostics } from './voice-diagnostics'

import {
  addCartItem,
  cancelCheckout,
  cancelPayOSPayment,
  closeSession,
  confirmCheckout,
  createRequestId,
  createSession,
  createSynthesisTicket,
  fetchProductCatalogIndex,
  getCart,
  getManagerNotifierStatus,
  getPayOSStatus,
  prewarmSpeech,
  prepareCheckout,
  respond,
  respondWithImage,
  setCartItemQuantity,
  simulateSandboxPayment,
  submitAssistedOrder,
  syncPayOSPayment,
  transcribe,
  type TextDeltaHandler,
} from './api'
import CartDrawer from './components/CartDrawer.vue'
import ChatPanel from './components/ChatPanel.vue'
import ProductCatalogModal from './components/ProductCatalogModal.vue'
import type {
  AssistedCartItem,
  AssistedOrderResponse,
  CheckoutQuote,
  ProductCard,
  SandboxOrder,
  ShoppingCart,
} from './types'
import {
  classifyVietnameseTurnCompleteness,
  isLikelyAssistantEcho,
  isLikelyRepeatedSpeechTranscript,
} from './transcript-quality'
import { ensureAudioLipSyncTap, sampleAudioElementRms } from './lip-sync'
import { extractSpeakableChunks } from './speech-queue'
const MaiAvatar = defineAsyncComponent(() => import('./components/MaiVideoAvatar.vue'))
import { isLikelyVirtualMicrophone, listMicrophoneOptions, preferredMicrophoneId } from './microphone-devices'
import type { AssistantStatus, ChatMessage, LanguageCode } from './types'
import {
  VoiceConversationController,
  type VoiceControllerError,
  type VoiceState,
  type VoiceCaptureMetadata,
} from './voice-controller'
import { canUseStreamingStt, StreamingSttSession, type LiveStreamingSttHandle } from './voice-stream-stt'
import {
  AzureDirectStreamingSttSession,
  clearAzureDirectTokenCache,
  warmAzureDirectStt,
} from './voice-azure-direct-stt'
import {
  closeDirectAzureTts,
  synthesizeDirectAzureAudioBlob,
  warmDirectAzureTts,
} from './voice-azure-direct-tts'
import { warmBrowserGtcrnDenoiser } from './voice-gtcrn-denoiser'

const SILENT_AUDIO_DATA_URI =
  'data:audio/wav;base64,UklGRnQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YVAAAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgA=='
const sessionId = ref<string | null>(null)
const messages = ref<ChatMessage[]>([])
const cart = ref<ShoppingCart | null>(null)
const checkoutQuote = ref<CheckoutQuote | null>(null)
const sandboxOrder = ref<SandboxOrder | null>(null)
const ASSISTED_CART_STORAGE_KEY = 'retail-ai-assisted-cart-v1'
const PENDING_PAYOS_STORAGE_KEY = 'retail-ai-pending-payos-v1'
const assistedCart = ref<AssistedCartItem[]>([])
const assistedOrders = ref<AssistedOrderResponse[]>([])
const isCartOpen = ref(false)
const isCatalogOpen = ref(false)
const cartInitialTab = ref<'cart' | 'orders'>('cart')
const activeAssistedOrder = ref<AssistedOrderResponse | null>(null)
const isSubmittingAssistedOrder = ref(false)
const isCheckingPayOS = ref(false)
const payosEnabled = ref(false)
const assistedOrderError = ref<string | null>(null)
const cartToastMessage = ref<string | null>(null)
let cartToastTimer: ReturnType<typeof setTimeout> | undefined
let catalogPrefetchTimer: ReturnType<typeof setTimeout> | undefined
let payosPollTimer: ReturnType<typeof setInterval> | undefined

const assistedTotalCount = computed(() =>
  assistedCart.value.reduce((sum, entry) => sum + entry.quantity, 0),
)
const assistedTotalVnd = computed(() =>
  assistedCart.value.reduce((sum, entry) => sum + entry.product.price_vnd * entry.quantity, 0),
)
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
const isMobileSheetExpanded = ref(false)
const cameraTriggerToken = ref(0)
const managerNotifyChannel = ref<'zalo' | 'messenger' | 'gmail' | null>(null)
const messageImageBlobUrls = new Set<string>()
// The kiosk's active dialogue, STT and TTS share one Vietnamese locale.
const language = computed<LanguageCode>(() => 'vi')
const voiceHint = ref('Đang chuẩn bị chế độ hội thoại tiếng Việt')
const lastPrompt = ref('')
const audioElement = ref<HTMLAudioElement | null>(null)
const audioUnlocked = ref(false)
const needsAudioUnlock = ref(false)
const isAudioPlaying = ref(false)
const speechSourceMode = ref<'none' | 'cloud' | 'browser'>('none')
const speechBoundaryToken = ref(0)
const speechBoundaryWord = ref('')
const pendingSpeechText = ref<string | null>(null)
const voiceDebugAvailable = new URLSearchParams(window.location.search).get('voiceDebug') === '1'
const voiceDebugEnabled = ref(false)
const voiceDiagnostics = shallowReactive(new LocalVoiceDiagnostics())
let latestCapture: VoiceCaptureMetadata | undefined

function clearVoiceDiagnostics() {
  voiceDiagnostics.clear()
  latestCapture = undefined
}

function setVoiceDebug(event: Event) {
  voiceDebugEnabled.value = (event.target as HTMLInputElement).checked
  clearVoiceDiagnostics()
}

let isUnmounted = false
let audioPlayback: HTMLAudioElement | null = null
let audioUnlockPromise: Promise<boolean> | null = null
let speechRequestId = 0
let startedSpeechRequestId = 0
// Leave a full tail after TTS finishes so speaker hardware buffers and room
// reverberation settle completely before re-opening the microphone.
const SPEAKER_SETTLE_MS = 650
const STOP_SPEAKING_SETTLE_MS = 240
const ECHO_GUARD_WINDOW_MS = 25_000
const ECHO_STRICT_WINDOW_MS = 5_000
function isFullDuplexBargeInEnabled(): boolean {
  if (typeof window === 'undefined') return true
  const params = new URLSearchParams(window.location.search)
  if (params.get('halfDuplex') === '1' || params.get('fullDuplex') === '0') {
    return false
  }
  return true
}
let speechResumeTimer: ReturnType<typeof setTimeout> | undefined
let currentSpeechText: string | null = null
let bargeInEligible = false
let lastSpokenText = ''
let lastSpokenAt = 0
let lastBargeInAt = 0
let recentSpokenHistory: Array<{ text: string; at: number }> = []
const activePrefetchedBlobUrls = new Set<string>()
let voiceController: VoiceConversationController | null = null
let keepExplicitMicrophoneChoice = false
let listeningStartedAt = 0
let sessionExpiryTimer: ReturnType<typeof setTimeout> | undefined
let sessionIdleTimeoutMs = 30 * 60 * 1000
let sessionGeneration = 0
let bootInFlight: { generation: number; promise: Promise<void> } | null = null
let resetInFlight: Promise<void> | null = null
let pendingTurn: { text: string; generation: number; id: string } | null = null
let activeTurnController: AbortController | null = null
let speechPrewarmSessionId = ''
let speechPrewarmLastAttemptAt = 0
let speechPrewarmInFlight = false
let activeTranscriptionController: AbortController | null = null
let activeStreamStt: LiveStreamingSttHandle | null = null
let activeTurnFromVoice = false
let voiceTurnEpoch = 0
let streamingSttAvailable: boolean | undefined
let directAzureSttAvailable = false
let hasOpenedMicrophoneOnce = false
const autoSwitchedMicIds = new Set<string>()
let bargeInCandidateAbort: AbortController | null = null

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
  noSpeech: string
  virtualMicrophone: string
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
    noSpeech: 'Mình chưa nghe thấy câu nói rõ ràng. Bạn nói lại nhé.',
    virtualMicrophone: 'Tab đang thu từ micro ảo. Nếu mức tín hiệu không nhảy, hãy chọn Microphone Array (AMD Audio Device) hoặc mở ứng dụng trong trình duyệt có micro thật.',
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
    noSpeech: 'I could not hear clear speech. Please say that again.',
    virtualMicrophone: 'This tab is using a virtual microphone. If the signal meter does not move, choose the physical microphone or open the kiosk in a browser with microphone access.',
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
    noSpeech: '没有听到清楚的话，请再说一次。',
    virtualMicrophone: '当前标签页正在使用虚拟麦克风。如果信号条不动，请选择实体麦克风，或在能访问实体麦克风的浏览器中打开应用。',
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
  if (language.value === 'en') return 'Click once to let Mai play audio.'
  if (language.value === 'zh') return '请点击一次，让 Mai 播放语音。'
  return 'Bấm một lần để cho phép Mai phát giọng nói.'
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

function recordSpokenEchoText(text: string | null | undefined) {
  const trimmed = text?.trim()
  if (!trimmed) return
  const now = Date.now()
  lastSpokenText = trimmed
  lastSpokenAt = now
  recentSpokenHistory = recentSpokenHistory
    .filter((entry) => now - entry.at < ECHO_GUARD_WINDOW_MS && entry.text !== trimmed)
  recentSpokenHistory.push({ text: trimmed, at: now })
  if (recentSpokenHistory.length > 12) recentSpokenHistory.shift()
}

function getCurrentPlaybackLevel(): number {
  if (!isAudioPlaying.value && status.value !== 'speaking') return 0
  if (speechSourceMode.value === 'cloud') {
    if (!audioPlayback || audioPlayback.paused || audioPlayback.ended) return 0
    const rms = sampleAudioElementRms(audioPlayback)
    return rms !== null ? rms : 0.08
  }
  if (speechSourceMode.value === 'browser' && isAudioPlaying.value) {
    return 0.12
  }
  return 0
}

function isEchoOfRecentAssistantSpeech(transcript: string, forceStrict = false): boolean {
  const now = Date.now()
  const strict = forceStrict
    || currentSpeechText !== null
    || now - lastSpokenAt < ECHO_STRICT_WINDOW_MS
    || now - lastBargeInAt < ECHO_STRICT_WINDOW_MS
  const echoOptions = { strict }
  if (currentSpeechText && isLikelyAssistantEcho(transcript, currentSpeechText, echoOptions)) return true
  if (now - lastSpokenAt < ECHO_GUARD_WINDOW_MS && isLikelyAssistantEcho(transcript, lastSpokenText, echoOptions)) {
    return true
  }
  const activeHistory = recentSpokenHistory.filter((entry) => now - entry.at < ECHO_GUARD_WINDOW_MS)
  if (!activeHistory.length) return false
  for (const entry of activeHistory) {
    if (isLikelyAssistantEcho(transcript, entry.text, echoOptions)) return true
  }
  const combined = activeHistory.map((entry) => entry.text).join(' ')
  return isLikelyAssistantEcho(transcript, combined, echoOptions)
}

function isRealBargeInSpeech(transcript: string): boolean {
  const cleaned = transcript.trim()
  if (!cleaned) return false
  const letters = cleaned.match(/\p{L}/gu)
  if (!letters || letters.length < 2) return false
  if (isLikelyRepeatedSpeechTranscript(cleaned)) return false
  if (isEchoOfRecentAssistantSpeech(cleaned, true)) return false
  return true
}

function isStopSpeechCommand(transcript: string): boolean {
  const normalized = transcript
    .replace(/đ/g, 'd')
    .replace(/Đ/g, 'd')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^\p{L}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  return /^(?:dung|dung lai|ngung|ngung lai|thoi|thoi duoc roi|im|im di|tat tieng|khoan|khoan da|cho chut|cho minh chut|stop|pause|quiet|wait)$/.test(normalized)
}

function stopBargeInCandidateVerification() {
  bargeInCandidateAbort?.abort()
  bargeInCandidateAbort = null
}

async function handleBargeInCandidateAudio(audio: Blob) {
  if (!sessionId.value || !isHandsFree.value || isUnmounted || !bargeInEligible) {
    return
  }
  const activeSessionId = sessionId.value
  const generation = sessionGeneration
  const voiceEpoch = voiceTurnEpoch
  bargeInCandidateAbort?.abort()
  const controller = new AbortController()
  bargeInCandidateAbort = controller
  try {
    const result = await transcribe(activeSessionId, audio, language.value, controller.signal, false)
    if (
      isUnmounted
      || generation !== sessionGeneration
      || voiceEpoch !== voiceTurnEpoch
      || !isHandsFree.value
      || !bargeInEligible
    ) {
      return
    }
    if (result.speech_detected === false || !isRealBargeInSpeech(result.text)) {
      // Fan noise or loudspeaker echo — keep Mai talking without any interruption!
      return
    }
    lastBargeInAt = Date.now()
    stopBargeInCandidateVerification()
    invalidateSpeechPlayback()
    voiceController?.setBargeInMode?.(false)
    if (isStopSpeechCommand(result.text)) {
      status.value = 'listening'
      voiceHint.value = 'Mình đã dừng lại, bạn cần hỏi gì cứ nói nhé'
      void resumeHandsFree()
      return
    }
    await sendMessage(result.text, true)
  } catch {
    // Never break active playback on background verification error.
  } finally {
    if (bargeInCandidateAbort === controller) {
      bargeInCandidateAbort = null
    }
  }
}

function handleVoiceBargeIn() {
  if (!isHandsFree.value || isUnmounted || !bargeInEligible) return
  if (status.value !== 'speaking' && currentSpeechText === null && !audioPlayback) return
  lastBargeInAt = Date.now()
  stopBargeInCandidateVerification()
  if (currentSpeechText) {
    recordSpokenEchoText(currentSpeechText)
  }
  invalidateSpeechPlayback()
  voiceController?.setBargeInMode?.(false)
  status.value = 'listening'
  isVoiceActive.value = true
  isRecording.value = true
  voiceHint.value = languageSettings[language.value].recording
}

function handleVoiceState(state: VoiceState) {
  if (bargeInEligible && isHandsFree.value && (status.value === 'speaking' || currentSpeechText !== null)) {
    if (state.phase === 'recording') {
      handleVoiceBargeIn()
      return
    }
    if (state.phase === 'listening') {
      isVoiceActive.value = true
      isRecording.value = false
      return
    }
    if (state.mode === 'idle') {
      isVoiceActive.value = false
      isRecording.value = false
      return
    }
  }
  if (state.mode !== 'idle' && voiceInputBlocked()) return
  isVoiceActive.value = state.mode !== 'idle'
  isRecording.value = state.phase === 'recording'
  if (state.phase === 'listening') {
    hasOpenedMicrophoneOnce = true
    maybePrewarmSpeech()
    listeningStartedAt = Date.now()
    void refreshMicrophones()
    if (status.value !== 'speaking') {
      status.value = 'listening'
      voiceHint.value = isLikelyVirtualMicrophone(activeMicrophone.value)
        ? languageSettings[language.value].virtualMicrophone
        : languageSettings[language.value].listening
    }
    return
  }
  if (state.phase === 'recording') {
    maybePrewarmSpeech()
    status.value = 'listening'
    voiceHint.value = languageSettings[language.value].recording
    return
  }
  if (state.mode === 'idle' && !['thinking', 'transcribing', 'speaking', 'error'].includes(status.value)) {
    status.value = 'idle'
  }
}

function maybePrewarmSpeech() {
  const activeSessionId = sessionId.value
  if (!activeSessionId || isUnmounted || speechPrewarmInFlight) return
  if (speechPrewarmSessionId !== activeSessionId) {
    speechPrewarmSessionId = activeSessionId
    speechPrewarmLastAttemptAt = 0
  }
  if (Date.now() - speechPrewarmLastAttemptAt < 45_000) return

  speechPrewarmLastAttemptAt = Date.now()
  if (directAzureSttAvailable && import.meta.env.MODE !== 'test') {
    // Pre-warm direct Browser -> Azure WSS TTS/STT instead of waking backend TTS.
    void warmAzureDirectStt(activeSessionId)
    void warmDirectAzureTts(activeSessionId)
    return
  }
  speechPrewarmInFlight = true
  void prewarmSpeech(activeSessionId, language.value)
    .catch(() => {
      // Speech prewarming is best-effort and must never block the conversation.
    })
    .finally(() => {
      speechPrewarmInFlight = false
    })
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
  if (!sessionId.value || isUnmounted || !isHandsFree.value || voiceInputBlocked()) return
  const generation = sessionGeneration
  const voiceEpoch = voiceTurnEpoch
  const requestedLanguage = language.value
  const includeDiagnostics = voiceDebugAvailable && voiceDebugEnabled.value
  const finishDiagnostics = includeDiagnostics
    ? voiceDiagnostics.begin(audio, requestedLanguage, latestCapture) : undefined
  latestCapture = undefined
  const transcriptionSessionId = sessionId.value
  const pendingStreamStt = activeStreamStt
  activeStreamStt = null
  // A later microphone turn supersedes any unfinished upload.
  activeTranscriptionController?.abort()
  const transcriptionController = new AbortController()
  activeTranscriptionController = transcriptionController
  status.value = 'transcribing'
  voiceHint.value = languageSettings[language.value].transcribing
  try {
    let result = null
    if (pendingStreamStt && !includeDiagnostics && pendingStreamStt.isHealthy) {
      const streamed = await pendingStreamStt.finish(3_000)
      if (
        streamed
        && streamed.session_id === transcriptionSessionId
        && streamed.speech_detected !== false
        && streamed.text.trim()
      ) {
        result = streamed
      }
    } else {
      pendingStreamStt?.cancel()
    }
    if (!result) {
      result = await withSessionRecovery(
        (id) => transcribe(id, audio, requestedLanguage, transcriptionController.signal, includeDiagnostics),
      )
    }
    if (generation !== sessionGeneration || isUnmounted || voiceEpoch !== voiceTurnEpoch || !isHandsFree.value) return
    if (result.session_id !== transcriptionSessionId) throw new Error('Phiên âm thanh không thuộc phiên hội thoại hiện tại.')
    finishDiagnostics?.(result)
    if (
      result.speech_detected === false
      || !result.text.trim()
      || isLikelyRepeatedSpeechTranscript(result.text)
    ) {
      await rejectVoiceTranscript()
      return
    }
    if (isEchoOfRecentAssistantSpeech(result.text)) {
      await rejectVoiceTranscript(true)
      return
    }
    if (result.needs_confirmation) {
      // Keep the heard words editable. A second decoder is a disagreement
      // signal, not permission to silently invent the customer's intention.
      toggleRecording()
      draft.value = result.text
      isTextOpen.value = true
      voiceHint.value = 'Mình nghe chưa chắc câu này. Bạn xem lại phần chữ rồi gửi nhé.'
      return
    }
    await sendMessage(result.text, true)
  } catch (error) {
    if (generation !== sessionGeneration || isUnmounted || voiceEpoch !== voiceTurnEpoch || !isHandsFree.value) return
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
  } finally {
    if (activeTranscriptionController === transcriptionController) {
      activeTranscriptionController = null
    }
  }
}

async function rejectVoiceTranscript(silentEcho = false) {
  errorMessage.value = null
  status.value = 'idle'
  voiceHint.value = silentEcho
    ? languageSettings[language.value].listening
    : languageSettings[language.value].noSpeech
  await resumeHandsFree()
  if (isHandsFree.value) {
    voiceHint.value = silentEcho
      ? languageSettings[language.value].listening
      : languageSettings[language.value].noSpeech
  }
}

function ensureVoiceController(): VoiceConversationController {
  if (!voiceController) {
    voiceController = new VoiceConversationController({
      locale: languageSettings[language.value].locale,
      preferRecorder: true,
      preferPcmWorklet: true,
      preferNeuralVad: true,
      requireSttForBargeIn: true,
      validateSpeechEnvelope: true,
      deviceId: selectedMicrophone.value,
      getPlaybackLevel: getCurrentPlaybackLevel,
      shouldHoldTurnEnd: async () => {
        const stream = activeStreamStt
        const partial = stream?.latestPartialText ?? ''
        if (language.value === 'vi' && partial) {
          const lexical = classifyVietnameseTurnCompleteness(partial)
          if (lexical === 'complete') return 0
          if (lexical === 'incomplete') return 1_050
        }
        if (stream && stream.isHealthy && stream.checkTurn) {
          const turn = await stream.checkTurn(240)
          if (turn && !turn.complete) {
            return Math.max(0, Math.min(1_200, turn.holdMs || 1_050))
          }
        }
        return 0
      },
      onState: handleVoiceState,
      onBargeIn: handleVoiceBargeIn,
      onBargeInCandidateAudio: (audio) => { void handleBargeInCandidateAudio(audio) },
      onTranscript: (text) => {
        if (!isHandsFree.value || isUnmounted) return
        if (bargeInEligible && (status.value === 'speaking' || currentSpeechText !== null)) {
          if (isLikelyAssistantEcho(text, currentSpeechText, { strict: true })) return
          handleVoiceBargeIn()
        }
        if (voiceInputBlocked()) return
        if (isLikelyRepeatedSpeechTranscript(text)) {
          void rejectVoiceTranscript()
          return
        }
        if (isEchoOfRecentAssistantSpeech(text)) {
          void rejectVoiceTranscript(true)
          return
        }
        void sendMessage(text, true)
      },
      onAudio: (audio) => { void handleVoiceAudio(audio) },
      onSpeechStartFrame: (preRollFrames) => {
        activeStreamStt?.cancel()
        activeStreamStt = null
        if (
          !sessionId.value
          || !isHandsFree.value
          || voiceInputBlocked()
          || (voiceDebugAvailable && voiceDebugEnabled.value)
        ) {
          return
        }
        const sttSessionId = sessionId.value
        const sttLanguage = language.value
        const sttEpoch = voiceTurnEpoch
        const handlePartial = (partial: string) => {
          if (isUnmounted || sttEpoch !== voiceTurnEpoch || !isHandsFree.value) return
          if (isEchoOfRecentAssistantSpeech(partial, true)) return
          if (status.value === 'listening' || status.value === 'transcribing') {
            voiceHint.value = sttLanguage === 'en'
              ? `Hearing: "${partial}"`
              : sttLanguage === 'zh'
                ? `正在听：“${partial}”`
                : `Đang nghe: “${partial}”`
          }
        }
        let stream: LiveStreamingSttHandle | null = null
        if (canUseStreamingStt(streamingSttAvailable)) {
          stream = new StreamingSttSession({
            sessionId: sttSessionId,
            language: sttLanguage,
            onPartial: handlePartial,
          })
        } else if (directAzureSttAvailable && import.meta.env.MODE !== 'test') {
          stream = new AzureDirectStreamingSttSession({
            sessionId: sttSessionId,
            language: sttLanguage,
            onPartial: handlePartial,
          })
        }
        if (!stream) return
        activeStreamStt = stream
        for (const preFrame of preRollFrames) stream.pushFrame(preFrame)
      },
      onSpeechFrame: (frame) => {
        activeStreamStt?.pushFrame(frame)
      },
      onSpeechMisfire: () => {
        activeStreamStt?.cancel()
        activeStreamStt = null
      },
      onCapture: (metadata) => {
        latestCapture = voiceDebugAvailable && voiceDebugEnabled.value ? metadata : undefined
      },
      onLevel: (level) => {
        microphoneLevel.value = level
        if (status.value === 'listening' && isVoiceActive.value && !isRecording.value && Date.now() - listeningStartedAt > 8_000) {
          const inputIsSilent = level < 0.005
          voiceHint.value = language.value === 'en'
            ? inputIsSilent
              ? 'The selected microphone has almost no input. Choose the physical microphone that receives your voice.'
              : 'I have not heard a complete phrase yet. Speak a little closer and finish your sentence.'
            : language.value === 'zh'
              ? inputIsSilent
                ? '当前麦克风几乎没有输入。请选择能收到你声音的实体麦克风。'
                : '还没有听到完整句子。请靠近麦克风并说完一句话。'
              : inputIsSilent
                ? 'Micro đang chọn gần như không có tín hiệu. Hãy chọn micro thật đang thu được giọng của bạn.'
                : 'Chưa nghe được câu nói trọn vẹn. Bạn nói gần mic hơn và nói hết câu nhé.'
        }
      },
      onInputDevice: (label) => {
        activeMicrophone.value = label
        if (status.value === 'listening' && isLikelyVirtualMicrophone(label)) {
          voiceHint.value = languageSettings[language.value].virtualMicrophone
        }
      },
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
    const preferredId = preferredMicrophoneId(
      options,
      selectedMicrophone.value,
      savedId,
      keepExplicitMicrophoneChoice,
    )
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
      && isLikelyVirtualMicrophone(activeMicrophone.value)
      && preferredId
      && !autoSwitchedMicIds.has(preferredId)
      && normalizeDeviceLabel(activeMicrophone.value) !== normalizeDeviceLabel(selectedLabel)
    ) {
      autoSwitchedMicIds.add(preferredId)
      void changeMicrophone(preferredId)
    }
  } catch {
    // Capture can still work when the browser restricts device enumeration.
  }
}

async function changeMicrophone(deviceId: string) {
  const wasEnabled = isHandsFree.value
  pauseVoice()
  keepExplicitMicrophoneChoice = true
  selectedMicrophone.value = deviceId
  voiceController?.setInputDevice(deviceId)
  try {
    window.localStorage.setItem('retail-ai-microphone-device', deviceId)
    window.localStorage.setItem('retail-ai-microphone-explicit-choice', '1')
  } catch {
    // Keep the selection for this page even when it cannot be persisted.
  }
  activeMicrophone.value = ''
  if (wasEnabled) await startHandsFree()
}

async function startHandsFree() {
  if (isUnmounted || voiceInputBlocked()) return
  if (!sessionId.value) await bootKiosk()
  if (!sessionId.value || isUnmounted) return
  // Voice turns need visible feedback: show listening state, transcript and any errors.
  isTextOpen.value = true
  unlockAudio()
  isHandsFree.value = true
  // Warm up browser GTCRN neural denoiser (196 KB WASM) and direct Azure Speech STS token
  // + reusable Azure WSS TTS synthesizer in parallel so all audio models run client-side.
  void warmBrowserGtcrnDenoiser()
  if (directAzureSttAvailable && sessionId.value) {
    void warmAzureDirectStt(sessionId.value)
    void warmDirectAzureTts(sessionId.value)
  }
  voiceHint.value = hasOpenedMicrophoneOnce
    ? languageSettings[language.value].listening
    : (
      language.value === 'en'
        ? 'Opening the microphone. Allow microphone access if your browser asks.'
        : language.value === 'zh'
          ? '正在打开麦克风。如果浏览器询问，请允许使用麦克风。'
          : 'Đang mở micro. Hãy cho phép dùng micro nếu trình duyệt hỏi.'
    )
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
  stopBargeInCandidateVerification()
  activeStreamStt?.cancel()
  activeStreamStt = null
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
    audio.crossOrigin = 'anonymous'
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

function revokeBlobUrl(url: string | null | undefined) {
  if (!url || !activePrefetchedBlobUrls.has(url)) return
  activePrefetchedBlobUrls.delete(url)
  try {
    URL.revokeObjectURL(url)
  } catch {
    // Ignore revoke errors on unsupported environments.
  }
}

function revokeAllPrefetchedBlobUrls() {
  for (const url of activePrefetchedBlobUrls) {
    try {
      URL.revokeObjectURL(url)
    } catch {
      // Ignore revoke errors.
    }
  }
  activePrefetchedBlobUrls.clear()
}

function releaseAudioPlayback() {
  if (audioPlayback) {
    const previousSrc = audioPlayback.getAttribute('src')
    audioPlayback.onplay = null
    audioPlayback.onended = null
    audioPlayback.onerror = null
    audioPlayback.pause()
    audioPlayback.removeAttribute('src')
    revokeBlobUrl(previousSrc)
    audioPlayback = null
  }
}

interface QueuedSpeechItem {
  text: string
  ticketPromise: Promise<string | null>
}
let activeSpeechQueue: QueuedSpeechItem[] = []
let speechStreamOpen = false
let speechChunkPlaying = false
let speechQueueAfterCallback: (() => void) | undefined

function invalidateSpeechPlayback() {
  stopBargeInCandidateVerification()
  clearSpeechResume()
  speechRequestId += 1
  startedSpeechRequestId = 0
  currentSpeechText = null
  bargeInEligible = false
  isAudioPlaying.value = false
  speechSourceMode.value = 'none'
  pendingSpeechText.value = null
  activeSpeechQueue = []
  speechStreamOpen = false
  speechChunkPlaying = false
  speechQueueAfterCallback = undefined
  releaseAudioPlayback()
  revokeAllPrefetchedBlobUrls()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
}

function suspendMicForSpeech() {
  stopBargeInCandidateVerification()
  activeStreamStt?.cancel()
  activeStreamStt = null
  if (isHandsFree.value && isFullDuplexBargeInEnabled() && voiceController?.isActive) {
    voiceController.setBargeInMode?.(true)
    isRecording.value = false
    return
  }
  pauseVoice()
}

function armBargeIn(requestId: number) {
  if (!isHandsFree.value || isUnmounted || requestId !== speechRequestId) return
  // Optional half-duplex shield (?halfDuplex=1 / ?fullDuplex=0) keeps the mic paused
  // while Mai plays; shoppers interrupt via "Ngắt & nói" or Space.
  if (!isFullDuplexBargeInEnabled()) {
    bargeInEligible = false
    pauseVoice()
    voiceHint.value = language.value === 'vi'
      ? 'Đang trả lời — bấm "Ngắt & nói" hoặc phím Space nếu muốn nói ngay'
      : language.value === 'en'
        ? 'Speaking — tap Pause or press Space to interrupt'
        : '正在回答 — 点击暂停或按空格键打断'
    return
  }
  bargeInEligible = true
  voiceHint.value = language.value === 'vi'
    ? 'Đang trả lời — bạn cứ nói để ngắt lời hoặc bấm "Ngắt & nói"'
    : language.value === 'en'
      ? 'Speaking — speak clearly to interrupt'
      : '正在回答 — 清晰说话可打断'
  const controller = ensureVoiceController()
  controller.setBargeInMode?.(true)
  if (!controller.isActive) {
    void controller.start().catch(() => {
      // Keep TTS playing even if the microphone cannot re-arm immediately.
    })
  }
}

function markSpeechPlaybackStarted(requestId: number, text: string) {
  if (requestId !== speechRequestId || isUnmounted) return
  if (startedSpeechRequestId === requestId) return
  startedSpeechRequestId = requestId
  audioUnlocked.value = true
  needsAudioUnlock.value = false
  isAudioPlaying.value = true
  if (speechSourceMode.value === 'cloud' && audioPlayback) {
    ensureAudioLipSyncTap(audioPlayback)
  }
  status.value = 'speaking'
  recordSpokenEchoText(text)
  armBargeIn(requestId)
}

function speakWithBrowser(text: string, afterSpeech?: () => void, requestId = speechRequestId): boolean {
  if (!('speechSynthesis' in window) || typeof SpeechSynthesisUtterance === 'undefined') {
    isAudioPlaying.value = false
    speechSourceMode.value = 'none'
    bargeInEligible = false
    status.value = 'idle'
    resumeAfterSpeech(afterSpeech, requestId)
    return false
  }

  const synthesis = window.speechSynthesis
  synthesis.cancel()
  synthesis.resume?.()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = languageSettings[language.value].locale
  utterance.rate = 0.88
  utterance.pitch = 1.02
  const preferredLanguage = languageSettings[language.value].locale.slice(0, 2).toLowerCase()
  const preferredVoice = synthesis.getVoices().find((voice) => voice.lang.toLowerCase().startsWith(preferredLanguage))
  if (preferredVoice) utterance.voice = preferredVoice
  speechSourceMode.value = 'browser'
  utterance.onstart = () => {
    markSpeechPlaybackStarted(requestId, text)
  }
  utterance.onboundary = (event) => {
    if (requestId !== speechRequestId || isUnmounted) return
    const idx = typeof event.charIndex === 'number' ? event.charIndex : 0
    const len = typeof event.charLength === 'number' && event.charLength > 0 ? event.charLength : 10
    speechBoundaryWord.value = text.slice(idx, idx + len).split(/\s+/)[0] ?? ''
    speechBoundaryToken.value += 1
  }
  const finish = () => {
    if (requestId !== speechRequestId || isUnmounted) return
    isAudioPlaying.value = false
    speechSourceMode.value = 'none'
    bargeInEligible = false
    recordSpokenEchoText(text)
    if (currentSpeechText === text) currentSpeechText = null
    pendingSpeechText.value = null
    status.value = isHandsFree.value ? 'listening' : 'idle'
    resumeAfterSpeech(afterSpeech, requestId)
  }
  utterance.onend = finish
  utterance.onerror = finish
  status.value = 'speaking'
  synthesis.speak(utterance)
  return true
}

function startStreamedSpeech(afterSpeech?: () => void): number {
  clearSpeechResume()
  const requestId = ++speechRequestId
  startedSpeechRequestId = 0
  currentSpeechText = ''
  bargeInEligible = false
  isAudioPlaying.value = false
  speechSourceMode.value = 'none'
  activeSpeechQueue = []
  speechStreamOpen = true
  speechChunkPlaying = false
  speechQueueAfterCallback = afterSpeech
  suspendMicForSpeech()
  releaseAudioPlayback()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  status.value = 'speaking'
  voiceHint.value = language.value === 'vi' ? 'Mình đang trả lời bằng giọng nói' : statusHint.value
  return requestId
}

function prefetchQueuedAudioUrl(audioUrl: string, requestId: number): Promise<string> {
  if (
    typeof fetch !== 'function'
    || typeof URL === 'undefined'
    || typeof URL.createObjectURL !== 'function'
  ) {
    return Promise.resolve(audioUrl)
  }
  return fetch(audioUrl)
    .then(async (response) => {
      if (!response.ok || requestId !== speechRequestId || isUnmounted) return audioUrl
      const blob = await response.blob()
      if (!blob.size || requestId !== speechRequestId || isUnmounted) return audioUrl
      const blobUrl = URL.createObjectURL(blob)
      activePrefetchedBlobUrls.add(blobUrl)
      return blobUrl
    })
    .catch(() => audioUrl)
}

async function resolveSpeechAudioUrl(
  trimmedText: string,
  requestId: number,
  shouldPrefetchBackend: boolean,
): Promise<string | null> {
  if (!sessionId.value || typeof Audio === 'undefined') return null
  // Priority 1: Direct Browser -> Azure Neural TTS over WSS (0 Vercel calls, 0 server load!)
  if (
    directAzureSttAvailable
    && import.meta.env.MODE !== 'test'
    && typeof URL !== 'undefined'
    && typeof URL.createObjectURL === 'function'
  ) {
    const directBlob = await synthesizeDirectAzureAudioBlob(
      sessionId.value,
      trimmedText,
      language.value,
    )
    if (requestId !== speechRequestId || isUnmounted) return null
    if (directBlob && directBlob.size > 0) {
      const blobUrl = URL.createObjectURL(directBlob)
      activePrefetchedBlobUrls.add(blobUrl)
      return blobUrl
    }
  }
  // Priority 2: Fallback to backend synthesis ticket stream
  try {
    const audioUrl = await withSessionRecovery((id) =>
      createSynthesisTicket(id, trimmedText, language.value),
    )
    if (requestId !== speechRequestId || isUnmounted) return null
    return shouldPrefetchBackend ? await prefetchQueuedAudioUrl(audioUrl, requestId) : audioUrl
  } catch {
    return null
  }
}

function enqueueStreamedSpeech(requestId: number, chunkText: string) {
  const trimmed = chunkText.trim()
  if (!trimmed || requestId !== speechRequestId || isUnmounted) return
  currentSpeechText = currentSpeechText ? `${currentSpeechText} ${trimmed}` : trimmed
  recordSpokenEchoText(currentSpeechText)
  // Stream the first sentence directly for minimum start latency, while eagerly
  // prefetching MP3 bytes for subsequent queued sentences so transitions are gapless.
  const shouldPrefetchBytes = speechChunkPlaying || activeSpeechQueue.length > 0
  const ticketPromise: Promise<string | null> = resolveSpeechAudioUrl(
    trimmed,
    requestId,
    shouldPrefetchBytes,
  )
  activeSpeechQueue.push({ text: trimmed, ticketPromise })
  void drainSpeechQueue(requestId)
}

function finishStreamedSpeech(requestId: number, fullText: string) {
  if (requestId !== speechRequestId || isUnmounted) return
  if (fullText.trim()) {
    currentSpeechText = fullText
    recordSpokenEchoText(fullText)
  }
  speechStreamOpen = false
  void drainSpeechQueue(requestId)
}

function speakChunkWithBrowser(text: string, requestId: number, onDone: () => void) {
  if (!('speechSynthesis' in window) || typeof SpeechSynthesisUtterance === 'undefined') {
    onDone()
    return
  }
  const synthesis = window.speechSynthesis
  synthesis.cancel()
  synthesis.resume?.()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = languageSettings[language.value].locale
  utterance.rate = 0.88
  utterance.pitch = 1.02
  const preferredLanguage = languageSettings[language.value].locale.slice(0, 2).toLowerCase()
  const preferredVoice = synthesis.getVoices().find((voice) => voice.lang.toLowerCase().startsWith(preferredLanguage))
  if (preferredVoice) utterance.voice = preferredVoice
  speechSourceMode.value = 'browser'
  utterance.onstart = () => {
    recordSpokenEchoText(text)
    if (currentSpeechText) recordSpokenEchoText(currentSpeechText)
    markSpeechPlaybackStarted(requestId, currentSpeechText || text)
  }
  utterance.onboundary = (event) => {
    if (requestId !== speechRequestId || isUnmounted) return
    const idx = typeof event.charIndex === 'number' ? event.charIndex : 0
    const len = typeof event.charLength === 'number' && event.charLength > 0 ? event.charLength : 10
    speechBoundaryWord.value = text.slice(idx, idx + len).split(/\s+/)[0] ?? ''
    speechBoundaryToken.value += 1
  }
  const finish = () => {
    if (requestId !== speechRequestId || isUnmounted) return
    recordSpokenEchoText(text)
    onDone()
  }
  utterance.onend = finish
  utterance.onerror = finish
  status.value = 'speaking'
  synthesis.speak(utterance)
}

async function drainSpeechQueue(requestId: number) {
  if (requestId !== speechRequestId || isUnmounted || speechChunkPlaying) return
  const next = activeSpeechQueue.shift()
  if (!next) {
    if (!speechStreamOpen) {
      isAudioPlaying.value = false
      speechSourceMode.value = 'none'
      bargeInEligible = false
      if (currentSpeechText) recordSpokenEchoText(currentSpeechText)
      else lastSpokenAt = Date.now()
      releaseAudioPlayback()
      pendingSpeechText.value = null
      currentSpeechText = null
      status.value = isHandsFree.value ? 'listening' : 'idle'
      const cb = speechQueueAfterCallback
      speechQueueAfterCallback = undefined
      resumeAfterSpeech(cb, requestId)
    }
    return
  }

  speechChunkPlaying = true
  const onChunkDone = () => {
    if (requestId !== speechRequestId || isUnmounted) return
    speechChunkPlaying = false
    recordSpokenEchoText(next.text)
    if (currentSpeechText) recordSpokenEchoText(currentSpeechText)
    void drainSpeechQueue(requestId)
  }

  try {
    const audioUrl = await next.ticketPromise
    if (requestId !== speechRequestId || isUnmounted) return
    if (!audioUrl || typeof Audio === 'undefined') {
      speakChunkWithBrowser(next.text, requestId, onChunkDone)
      return
    }
    pendingSpeechText.value = currentSpeechText || next.text
    const audio = audioElement.value ?? new Audio()
    if (!audioElement.value) audioElement.value = audio
    audio.crossOrigin = 'anonymous'
    audio.preload = 'auto'
    audio.src = audioUrl
    audioPlayback = audio
    speechSourceMode.value = 'cloud'
    audio.onplay = () => {
      if (requestId !== speechRequestId || isUnmounted) return
      isAudioPlaying.value = true
      speechSourceMode.value = 'cloud'
      ensureAudioLipSyncTap(audio)
      recordSpokenEchoText(next.text)
      if (currentSpeechText) recordSpokenEchoText(currentSpeechText)
      markSpeechPlaybackStarted(requestId, currentSpeechText || next.text)
    }
    audio.onended = () => {
      if (requestId !== speechRequestId || isUnmounted) return
      releaseAudioPlayback()
      onChunkDone()
    }
    audio.onerror = () => {
      if (requestId !== speechRequestId || isUnmounted) return
      releaseAudioPlayback()
      speakChunkWithBrowser(next.text, requestId, onChunkDone)
    }
    try {
      await audio.play()
      if (requestId !== speechRequestId || isUnmounted) return
      isAudioPlaying.value = true
      speechSourceMode.value = 'cloud'
      ensureAudioLipSyncTap(audio)
      recordSpokenEchoText(next.text)
      if (currentSpeechText) recordSpokenEchoText(currentSpeechText)
      markSpeechPlaybackStarted(requestId, currentSpeechText || next.text)
    } catch (error) {
      if (requestId !== speechRequestId || isUnmounted) return
      if (isAutoplayBlocked(error)) {
        isAudioPlaying.value = false
        speechSourceMode.value = 'none'
        bargeInEligible = false
        speechChunkPlaying = false
        activeSpeechQueue = []
        speechStreamOpen = false
        needsAudioUnlock.value = true
        status.value = 'idle'
        voiceHint.value = audioUnlockHint.value
        return
      }
      releaseAudioPlayback()
      speakChunkWithBrowser(next.text, requestId, onChunkDone)
    }
  } catch {
    if (requestId !== speechRequestId || isUnmounted) return
    releaseAudioPlayback()
    speakChunkWithBrowser(next.text, requestId, onChunkDone)
  }
}

async function speak(text: string, afterSpeech?: () => void) {
  clearSpeechResume()
  const requestId = ++speechRequestId
  startedSpeechRequestId = 0
  currentSpeechText = text
  lastSpokenText = text
  bargeInEligible = false
  isAudioPlaying.value = false
  speechSourceMode.value = 'none'
  suspendMicForSpeech()
  releaseAudioPlayback()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  status.value = 'speaking'
  voiceHint.value = language.value === 'vi' ? 'Mình đang trả lời bằng giọng nói' : statusHint.value
  try {
    if (!sessionId.value || typeof Audio === 'undefined') {
      throw new Error('Cloud audio playback is unavailable')
    }
    const audioUrl = await resolveSpeechAudioUrl(text, requestId, false)
    if (requestId !== speechRequestId || isUnmounted) return
    if (!audioUrl) {
      throw new Error('Cloud audio playback is unavailable')
    }
    pendingSpeechText.value = text
    const audio = audioElement.value ?? new Audio()
    if (!audioElement.value) audioElement.value = audio
    audio.crossOrigin = 'anonymous'
    audio.preload = 'auto'
    audio.src = audioUrl
    audioPlayback = audio
    speechSourceMode.value = 'cloud'
    audio.onplay = () => {
      markSpeechPlaybackStarted(requestId, text)
    }
    audio.onended = () => {
      if (requestId !== speechRequestId || isUnmounted) return
      isAudioPlaying.value = false
      speechSourceMode.value = 'none'
      bargeInEligible = false
      recordSpokenEchoText(text)
      releaseAudioPlayback()
      pendingSpeechText.value = null
      if (currentSpeechText === text) currentSpeechText = null
      status.value = isHandsFree.value ? 'listening' : 'idle'
      resumeAfterSpeech(afterSpeech, requestId)
    }
    audio.onerror = () => {
      if (requestId !== speechRequestId || isUnmounted) return
      isAudioPlaying.value = false
      speechSourceMode.value = 'none'
      bargeInEligible = false
      pendingSpeechText.value = null
      releaseAudioPlayback()
      speakWithBrowser(text, afterSpeech, requestId)
    }
    try {
      await audio.play()
      markSpeechPlaybackStarted(requestId, text)
    } catch (error) {
      if (requestId !== speechRequestId || isUnmounted) return
      if (isAutoplayBlocked(error)) {
        isAudioPlaying.value = false
        speechSourceMode.value = 'none'
        bargeInEligible = false
        needsAudioUnlock.value = true
        status.value = 'idle'
        voiceHint.value = audioUnlockHint.value
        return
      }
      throw error
    }
  } catch {
    if (requestId !== speechRequestId || isUnmounted) return
    isAudioPlaying.value = false
    speechSourceMode.value = 'none'
    bargeInEligible = false
    releaseAudioPlayback()
    pendingSpeechText.value = null
    speakWithBrowser(text, afterSpeech, requestId)
  }
}

function voiceInputBlocked() {
  return currentSpeechText !== null || speechResumeTimer !== undefined || status.value === 'thinking'
}

function clearSpeechResume() {
  if (speechResumeTimer !== undefined) clearTimeout(speechResumeTimer)
  speechResumeTimer = undefined
}

function resumeAfterSpeech(
  afterSpeech: (() => void) | undefined,
  requestId: number,
  settleMs = SPEAKER_SETTLE_MS,
) {
  stopBargeInCandidateVerification()
  clearSpeechResume()
  currentSpeechText = null
  bargeInEligible = false
  isAudioPlaying.value = false
  speechSourceMode.value = 'none'
  if (voiceController?.isActive) {
    voiceController.setBargeInMode?.(true, settleMs)
  }
  speechResumeTimer = setTimeout(() => {
    speechResumeTimer = undefined
    if (requestId !== speechRequestId || isUnmounted) return
    if (isHandsFree.value && voiceController?.isActive) {
      voiceController.setBargeInMode?.(false)
      status.value = 'listening'
      isVoiceActive.value = true
      listeningStartedAt = Date.now()
      voiceHint.value = isLikelyVirtualMicrophone(activeMicrophone.value)
        ? languageSettings[language.value].virtualMicrophone
        : languageSettings[language.value].listening
      return
    }
    afterSpeech?.()
  }, settleMs)
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
      streamingSttAvailable = session.streaming_stt_available
      directAzureSttAvailable = Boolean(session.direct_azure_stt_available)
      const expiresAt = Date.parse(session.expires_at)
      const remainingMs = Number.isFinite(expiresAt) ? expiresAt - Date.now() : 30 * 60 * 1000
      // Guard against client clocks ahead of UTC triggering an instant 0ms reset loop on boot.
      sessionIdleTimeoutMs = remainingMs > 5_000 ? remainingMs : 30 * 60 * 1000
      if (Number.isFinite(expiresAt)) {
        scheduleSessionExpiry(
          remainingMs > 5_000
            ? session.expires_at
            : new Date(Date.now() + sessionIdleTimeoutMs).toISOString(),
        )
      }
      if (generation === sessionGeneration && sessionId.value === session.session_id) {
        voiceHint.value = languageSettings[language.value].ready
      }
      // Fetch initial cart in the background only when the active catalog supports
      // branch-verified cart transactions (avoids unnecessary 503 calls in snapshot mode).
      if (session.cart_available === false) {
        cart.value = null
      } else {
        void getCart(session.session_id)
          .then((initialCart) => {
            if (generation === sessionGeneration && sessionId.value === session.session_id) {
              cart.value = initialCart
            }
          })
          .catch(() => {
            // Catalog configuration can disable cart actions while text lookup is still available.
            if (generation === sessionGeneration && sessionId.value === session.session_id) {
              cart.value = null
            }
          })
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
  clearVoiceDiagnostics()

  const generation = ++sessionGeneration
  voiceTurnEpoch += 1
  const oldSessionId = sessionId.value
  activeTurnController?.abort()
  activeTurnController = null
  activeTranscriptionController?.abort()
  activeTranscriptionController = null
  activeTurnFromVoice = false
  if (sessionExpiryTimer) clearTimeout(sessionExpiryTimer)
  sessionExpiryTimer = undefined
  speechRequestId += 1
  startedSpeechRequestId = 0
  currentSpeechText = null
  bargeInEligible = false
  isAudioPlaying.value = false
  lastSpokenText = ''
  lastSpokenAt = 0
  lastBargeInAt = 0
  recentSpokenHistory = []
  activeSpeechQueue = []
  speechStreamOpen = false
  speechChunkPlaying = false
  speechQueueAfterCallback = undefined
  clearSpeechResume()
  pendingSpeechText.value = null
  pauseVoice()
  releaseAudioPlayback()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  sessionId.value = null
  directAzureSttAvailable = false
  clearAzureDirectTokenCache()
  closeDirectAzureTts()
  for (const url of messageImageBlobUrls) {
    try { URL.revokeObjectURL(url) } catch { /* ignore */ }
  }
  messageImageBlobUrls.clear()
  messages.value = []
  pendingTurn = null
  draft.value = ''
  cart.value = null
  checkoutQuote.value = null
  sandboxOrder.value = null
  assistedCart.value = []
  assistedOrders.value = []
  activeAssistedOrder.value = null
  assistedOrderError.value = null
  isCartOpen.value = false
  isCatalogOpen.value = false
  stopPayOSPolling()
  persistPendingPayOS(null)
  persistAssistedCart()
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
  unsentText?: string,
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
    if (unsentText && !isUnmounted && sessionGeneration === generation + 1) {
      // Preserve only the current rejected text for an explicit resend. Never
      // replay an action/audio or restore another customer's prior history.
      draft.value = unsentText
      isTextOpen.value = true
      voiceHint.value = 'Phiên trước đã kết thúc. Mình giữ câu vừa nhập trong ô bên dưới; bạn bấm gửi lại nhé.'
    }
    throw new Error('Phiên trước đã kết thúc. Hội thoại cũ đã được xóa; vui lòng nói lại yêu cầu.')
  }
}

async function sendMessage(value = draft.value, fromVoice = false, isRetry = false) {
  const text = value.trim()
  const voiceEpoch = voiceTurnEpoch
  if (!text || status.value === 'thinking' || (status.value === 'transcribing' && !fromVoice)) return
  // An accepted customer turn takes over. Cancel any cloud or browser speech
  // before the new request starts so late media events cannot reset its state.
  invalidateSpeechPlayback()
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
  const turn = { text, generation, id: clientTurnId }
  pendingTurn = turn
  const assistantMessage = pushMessage('assistant', '')
  const turnController = new AbortController()
  activeTurnController = turnController
  activeTurnFromVoice = fromVoice
  status.value = 'thinking'
  voiceHint.value = languageSettings[language.value].thinking

  let streamedSpeechBuffer = ''
  let streamedSpeechStarted = false
  let streamedSpeechRequestId = 0

  try {
    let requestedSessionId: string | null = null
    const handleTextDelta: TextDeltaHandler = (delta) => {
      if (generation !== sessionGeneration || isUnmounted
        || (fromVoice && voiceEpoch !== voiceTurnEpoch)) {
        return
      }
      assistantMessage.text += delta
      if (fromVoice && isHandsFree.value) {
        streamedSpeechBuffer += delta
        const { chunks, remainder } = extractSpeakableChunks(streamedSpeechBuffer, false)
        streamedSpeechBuffer = remainder
        for (const chunk of chunks) {
          if (!streamedSpeechStarted) {
            streamedSpeechStarted = true
            streamedSpeechRequestId = startStreamedSpeech(resumeHandsFree)
          }
          enqueueStreamedSpeech(streamedSpeechRequestId, chunk)
        }
      }
    }
    handleTextDelta.onStart = (meta) => {
      if (generation !== sessionGeneration || isUnmounted
        || (fromVoice && voiceEpoch !== voiceTurnEpoch)) {
        return
      }
      if (meta.products?.length) assistantMessage.products = meta.products
      if (meta.related_products?.length) assistantMessage.relatedProducts = meta.related_products
      if (meta.grounded_product_ids?.length) assistantMessage.groundedProductIds = meta.grounded_product_ids
      if (meta.model) assistantMessage.model = meta.model
    }
    const response = await withSessionRecovery((id) => {
      requestedSessionId = id
      return respond(
        id,
        text,
        language.value,
        clientTurnId,
        handleTextDelta,
        turnController.signal,
      )
    }, fromVoice ? undefined : text)
    if (generation !== sessionGeneration || isUnmounted) return
    if (fromVoice && (voiceEpoch !== voiceTurnEpoch || !isHandsFree.value)) {
      if (streamedSpeechStarted) invalidateSpeechPlayback()
      const assistantIndex = messages.value.findIndex((message) => message.id === assistantMessage.id)
      if (assistantIndex >= 0) messages.value.splice(assistantIndex, 1)
      if (pendingTurn === turn) pendingTurn = null
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
    assistantMessage.relatedProducts = response.related_products
    assistantMessage.shoppingPlan = response.shopping_plan
    assistantMessage.supportTicket = response.support_ticket ?? undefined
    if (pendingTurn === turn) pendingTurn = null
    if (response.cart) cart.value = response.cart
    if (response.checkout_quote) checkoutQuote.value = response.checkout_quote
    if (response.order) sandboxOrder.value = response.order
    if (fromVoice) {
      if (streamedSpeechStarted) {
        const flushed = extractSpeakableChunks(streamedSpeechBuffer, true)
        streamedSpeechBuffer = ''
        for (const chunk of flushed.chunks) {
          enqueueStreamedSpeech(streamedSpeechRequestId, chunk)
        }
        finishStreamedSpeech(streamedSpeechRequestId, response.text)
      } else {
        void speak(response.text, resumeHandsFree)
      }
    } else {
      status.value = 'idle'
      voiceHint.value = languageSettings[language.value].ready
      void resumeHandsFree()
    }
  } catch (error) {
    if (streamedSpeechStarted) invalidateSpeechPlayback()
    if (generation !== sessionGeneration || isUnmounted) return
    if (fromVoice && (voiceEpoch !== voiceTurnEpoch || !isHandsFree.value)) {
      const assistantIndex = messages.value.findIndex((message) => message.id === assistantMessage.id)
      if (assistantIndex >= 0) messages.value.splice(assistantIndex, 1)
      if (pendingTurn === turn) pendingTurn = null
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
  errorMessage.value = toErrorMessage(error, 'Chưa cập nhật được giỏ hàng. Bạn thử lại nhé.')
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
    pushMessage('assistant', 'Mình đã thêm sản phẩm vào giỏ hàng. Bạn có thể xem lại số lượng hoặc bắt đầu checkout.')
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
      await resetKioskSession('Thanh toán đã được ghi nhận trên hệ thống kiosk Thanh Lam Mart. Kiosk đã mở phiên mới.')
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

function persistAssistedCart() {
  try {
    if (!assistedCart.value.length) {
      window.sessionStorage.removeItem(ASSISTED_CART_STORAGE_KEY)
    } else {
      window.sessionStorage.setItem(ASSISTED_CART_STORAGE_KEY, JSON.stringify(assistedCart.value))
    }
  } catch {
    // Session storage may be blocked in strict privacy modes; in-memory cart still works.
  }
}

function restoreAssistedCart() {
  try {
    const raw = window.sessionStorage.getItem(ASSISTED_CART_STORAGE_KEY)
    if (!raw) return
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed)) {
      assistedCart.value = parsed.filter(
        (entry): entry is AssistedCartItem =>
          Boolean(entry?.product?.product_id && typeof entry.quantity === 'number' && entry.quantity > 0),
      )
    }
  } catch {
    // Ignore corrupt stored state.
  }
}

function showCartToast(message: string) {
  cartToastMessage.value = message
  if (cartToastTimer) clearTimeout(cartToastTimer)
  cartToastTimer = setTimeout(() => {
    cartToastMessage.value = null
    cartToastTimer = undefined
  }, 2_800)
}

function handleAddAssistedItem(product: ProductCard) {
  const existing = assistedCart.value.find((entry) => entry.product.product_id === product.product_id)
  if (existing) {
    if (existing.quantity < 20) {
      existing.quantity += 1
      existing.product = product
    }
  } else {
    if (assistedCart.value.length >= 20) {
      showCartToast('Giỏ hàng tối đa 20 loại sản phẩm mỗi đơn.')
      return
    }
    assistedCart.value.push({ product, quantity: 1 })
  }
  activeAssistedOrder.value = null
  assistedOrderError.value = null
  persistAssistedCart()
  const cleanName = product.name.replace(/\s+/g, ' ').trim()
  const shortName = cleanName.length > 34 ? `${cleanName.slice(0, 33)}…` : cleanName
  showCartToast(`Đã thêm "${shortName}" vào giỏ`)
}

function handleUpdateAssistedQty(productId: string, quantity: number) {
  if (quantity <= 0) {
    handleRemoveAssistedItem(productId)
    return
  }
  const target = assistedCart.value.find((entry) => entry.product.product_id === productId)
  if (!target) return
  target.quantity = Math.min(20, Math.max(1, quantity))
  assistedOrderError.value = null
  persistAssistedCart()
}

function handleRemoveAssistedItem(productId: string) {
  assistedCart.value = assistedCart.value.filter((entry) => entry.product.product_id !== productId)
  assistedOrderError.value = null
  persistAssistedCart()
}

function handleClearAssistedCart() {
  assistedCart.value = []
  assistedOrderError.value = null
  persistAssistedCart()
}

function handleOpenCart(tab: 'cart' | 'orders' = 'cart') {
  isCatalogOpen.value = false
  cartInitialTab.value = tab
  assistedOrderError.value = null
  isCartOpen.value = true
}

function prefetchCatalogInBackground() {
  if (typeof fetchProductCatalogIndex !== 'function') return
  void fetchProductCatalogIndex().catch(() => {
    // Prefetch is best-effort; modal retries automatically on open.
  })
}

function handleOpenCatalog() {
  isCartOpen.value = false
  isCatalogOpen.value = true
}

function handleBrowseCartSuggestions() {
  isCartOpen.value = false
  isCatalogOpen.value = true
}

function handleAskMaiFromCatalog(prompt: string) {
  isCatalogOpen.value = false
  isTextOpen.value = true
  void sendMessage(prompt)
}

function stopPayOSPolling() {
  if (payosPollTimer) {
    clearInterval(payosPollTimer)
    payosPollTimer = undefined
  }
}

function persistPendingPayOS(order: AssistedOrderResponse | null) {
  try {
    if (!order || !order.payos_order_code || order.payment_status !== 'PENDING_PAYOS') {
      window.sessionStorage.removeItem(PENDING_PAYOS_STORAGE_KEY)
      return
    }
    window.sessionStorage.setItem(
      PENDING_PAYOS_STORAGE_KEY,
      JSON.stringify({
        payosOrderCode: order.payos_order_code,
        paymentVerifyToken: order.payment_verify_token || undefined,
      }),
    )
  } catch {
    // Session storage may be blocked; polling still works in memory.
  }
}

function readPendingPayOS(): { payosOrderCode: number; paymentVerifyToken?: string } | null {
  try {
    const raw = window.sessionStorage.getItem(PENDING_PAYOS_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { payosOrderCode?: unknown; paymentVerifyToken?: unknown }
    const code = Number(parsed?.payosOrderCode)
    if (!Number.isFinite(code) || code <= 0) return null
    return {
      payosOrderCode: code,
      paymentVerifyToken: typeof parsed?.paymentVerifyToken === 'string' ? parsed.paymentVerifyToken : undefined,
    }
  } catch {
    return null
  }
}

function upsertAssistedOrder(order: AssistedOrderResponse) {
  const idx = assistedOrders.value.findIndex(
    (entry) => entry.order_id === order.order_id || entry.order_code === order.order_code,
  )
  if (idx >= 0) {
    const next = [...assistedOrders.value]
    next[idx] = order
    assistedOrders.value = next
  } else {
    assistedOrders.value = [order, ...assistedOrders.value]
  }
}

function finalizePaidPayOSOrder(synced: AssistedOrderResponse) {
  stopPayOSPolling()
  persistPendingPayOS(null)
  assistedOrderError.value = null
  activeAssistedOrder.value = synced
  upsertAssistedOrder(synced)
  assistedCart.value = []
  persistAssistedCart()
  const alreadyInChat = messages.value.some((msg) => msg.assistedOrder?.order_code === synced.order_code)
  if (!alreadyInChat) {
    pushMessage('assistant', synced.customer_message, {
      model: 'assisted-order',
      assistedOrder: synced,
    })
  }
}

async function handleCheckPayOSPayment(order: AssistedOrderResponse, silent = false) {
  const payosCode = order.payos_order_code
  if (!payosCode || isCheckingPayOS.value || isUnmounted) return
  if (typeof syncPayOSPayment !== 'function') return
  isCheckingPayOS.value = true
  if (!silent) assistedOrderError.value = null
  try {
    const synced = await syncPayOSPayment(payosCode, order.payment_verify_token)
    if (isUnmounted) return
    if (synced.payment_status === 'PAID') {
      finalizePaidPayOSOrder(synced)
      return
    }
    if (synced.payment_status === 'CANCELLED' || synced.payment_status === 'EXPIRED') {
      stopPayOSPolling()
      persistPendingPayOS(null)
      activeAssistedOrder.value = null
      assistedOrderError.value = synced.customer_message
        || 'Mã QR PayOS đã hủy hoặc hết hạn. Giỏ hàng vẫn được giữ để bạn tạo lại mã mới.'
      return
    }
    activeAssistedOrder.value = {
      ...order,
      ...synced,
      payment_verify_token: synced.payment_verify_token || order.payment_verify_token,
    }
    if (!silent) {
      assistedOrderError.value = 'Chưa thấy giao dịch mới trên PayOS. Nếu bạn vừa chuyển khoản xong, đợi 2–3 giây rồi bấm kiểm tra lại nhé.'
    }
  } catch (error) {
    if (!silent && !isUnmounted) {
      assistedOrderError.value = toErrorMessage(
        error,
        'Chưa kiểm tra được trạng thái thanh toán PayOS. Bạn thử bấm kiểm tra lại nhé.',
      )
    }
  } finally {
    if (!isUnmounted) isCheckingPayOS.value = false
  }
}

function startPayOSPolling(order: AssistedOrderResponse) {
  stopPayOSPolling()
  if (!order.payos_order_code || typeof syncPayOSPayment !== 'function') return
  payosPollTimer = setInterval(() => {
    const current = activeAssistedOrder.value
    if (isUnmounted || !current || current.payment_status !== 'PENDING_PAYOS') {
      stopPayOSPolling()
      return
    }
    void handleCheckPayOSPayment(current, true)
  }, 3_000)
}

function handleCancelPayOSPayment(order: AssistedOrderResponse) {
  stopPayOSPolling()
  persistPendingPayOS(null)
  assistedOrderError.value = null
  activeAssistedOrder.value = null
  if (order.payos_order_code && typeof cancelPayOSPayment === 'function') {
    void cancelPayOSPayment(order.payos_order_code).catch(() => {
      // Cancelling an unpaid link is best-effort; the shopper can continue immediately.
    })
  }
}

async function handleSubmitAssistedOrder(form: {
  paymentMethod?: 'payos' | 'counter'
  customerName: string
  customerPhone: string
  pickupLocation: string
  customerNote: string
}) {
  if (!assistedCart.value.length || isSubmittingAssistedOrder.value) return
  isSubmittingAssistedOrder.value = true
  assistedOrderError.value = null
  const idempotencyKey = createRequestId()
  const chosenMethod: 'payos' | 'counter' = form.paymentMethod === 'payos' && payosEnabled.value
    ? 'payos'
    : 'counter'
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  const payload = {
    items: assistedCart.value.map((entry) => ({
      product_id: entry.product.product_id,
      quantity: entry.quantity,
    })),
    payment_method: chosenMethod,
    return_url: chosenMethod === 'payos' && origin ? `${origin}/?payos=success` : undefined,
    cancel_url: chosenMethod === 'payos' && origin ? `${origin}/?payos=cancel` : undefined,
    customer_name: form.customerName || undefined,
    customer_phone: form.customerPhone || undefined,
    pickup_location: form.pickupLocation || undefined,
    customer_note: form.customerNote || undefined,
    idempotency_key: idempotencyKey,
  }
  try {
    if (!sessionId.value) await bootKiosk()
    if (!sessionId.value) throw new Error('Chưa kết nối được phiên kiosk. Bạn thử lại giúp mình nhé.')
    let response: AssistedOrderResponse
    try {
      response = await submitAssistedOrder(sessionId.value, payload)
    } catch (firstErr) {
      const failure = firstErr as { status?: number; message?: string }
      const sessionGone = /session (?:is )?(?:closed|expired|not found)/i.test(failure?.message ?? '')
      if (![404, 409, 410].includes(failure?.status ?? 0) || !sessionGone) throw firstErr
      // Recover seamlessly on serverless cold restart without dropping the shopper's cart.
      const freshSession = await createSession('kiosk-01')
      sessionId.value = freshSession.session_id
      response = await submitAssistedOrder(freshSession.session_id, payload)
    }
    if (isUnmounted) return
    if (response.payment_method === 'payos' && response.payment_status === 'PENDING_PAYOS') {
      activeAssistedOrder.value = response
      persistPendingPayOS(response)
      startPayOSPolling(response)
      return
    }
    stopPayOSPolling()
    persistPendingPayOS(null)
    upsertAssistedOrder(response)
    activeAssistedOrder.value = response
    assistedCart.value = []
    persistAssistedCart()
    pushMessage('assistant', response.customer_message, {
      model: 'assisted-order',
      assistedOrder: response,
    })
  } catch (error) {
    if (!isUnmounted) {
      assistedOrderError.value = toErrorMessage(
        error,
        chosenMethod === 'payos'
          ? 'Chưa tạo được mã QR PayOS. Bạn thử lại hoặc chọn "Nhờ nhân viên & trả tại quầy" nhé.'
          : 'Chưa gửi được đơn tới quản lý. Bạn vui lòng thử lại hoặc báo nhân viên tại quầy nhé.',
      )
    }
  } finally {
    if (!isUnmounted) isSubmittingAssistedOrder.value = false
  }
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

function handleCallManager() {
  isTextOpen.value = true
  void sendMessage(
    language.value === 'en'
      ? 'Please notify the store manager that I need assistance.'
      : language.value === 'zh'
        ? '请帮我联系店长。'
        : 'Mình muốn gọi quản lý giúp mình.',
  )
}

function toggleTextPanel() {
  isTextOpen.value = !isTextOpen.value
  if (!isTextOpen.value) {
    isMobileSheetExpanded.value = false
  }
}

function toggleMobileSheetSize() {
  isMobileSheetExpanded.value = !isMobileSheetExpanded.value
}

function openCameraForProduct() {
  isTextOpen.value = true
  cameraTriggerToken.value += 1
}

async function handleSendImage(payload: { file: Blob; previewUrl: string; message: string }) {
  if (!sessionId.value) await bootKiosk()
  if (!sessionId.value || isUnmounted) return

  const generation = sessionGeneration
  const caption = payload.message.trim() || (
    language.value === 'en' ? 'What product is this?' : 'Đây là sản phẩm gì?'
  )
  draft.value = ''
  errorMessage.value = null
  isTextOpen.value = true
  if (payload.previewUrl) messageImageBlobUrls.add(payload.previewUrl)
  pushMessage('user', caption, { imagePreviewUrl: payload.previewUrl || undefined })
  const assistantMessage = pushMessage('assistant', '')
  const turnController = new AbortController()
  activeTurnController?.abort()
  activeTurnController = turnController
  activeTurnFromVoice = false
  status.value = 'thinking'
  voiceHint.value = language.value === 'en'
    ? 'Identifying product from photo...'
    : 'Mình đang nhận diện sản phẩm trong ảnh...'

  try {
    const response = await withSessionRecovery((id) =>
      respondWithImage(id, payload.file, payload.message.trim(), language.value, turnController.signal),
    )
    if (generation !== sessionGeneration || isUnmounted) return
    assistantMessage.text = response.text
    assistantMessage.model = response.model
    assistantMessage.groundedProductIds = response.grounded_product_ids
    assistantMessage.products = response.products
    assistantMessage.relatedProducts = response.related_products
    if (response.cart) cart.value = response.cart
    if (isHandsFree.value || audioUnlocked.value) {
      void speak(response.text, isHandsFree.value ? resumeHandsFree : undefined)
    } else {
      status.value = 'idle'
      voiceHint.value = languageSettings[language.value].ready
    }
  } catch (error) {
    if (generation !== sessionGeneration || isUnmounted) return
    if (error instanceof DOMException && error.name === 'AbortError') return
    const assistantIndex = messages.value.findIndex((m) => m.id === assistantMessage.id)
    if (assistantIndex >= 0 && !assistantMessage.text) messages.value.splice(assistantIndex, 1)
    errorMessage.value = toErrorMessage(error, 'Chưa nhận diện được ảnh. Bạn thử chụp rõ hơn nhé.')
    status.value = 'error'
    voiceHint.value = languageSettings[language.value].retry
  } finally {
    if (activeTurnController === turnController) activeTurnController = null
  }
}

function toggleRecording() {
  unlockAudio()
  if (isHandsFree.value || voiceController?.isActive || isVoiceActive.value) {
    voiceTurnEpoch += 1
    if (activeTurnFromVoice) activeTurnController?.abort()
    activeTranscriptionController?.abort()
    activeTranscriptionController = null
    isHandsFree.value = false
    invalidateSpeechPlayback()
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
  invalidateSpeechPlayback()
  pauseVoice()
  status.value = 'idle'
  voiceHint.value = languageSettings[language.value].ready
  resumeAfterSpeech(resumeHandsFree, speechRequestId, STOP_SPEAKING_SETTLE_MS)
}

function handleGlobalKeydown(event: KeyboardEvent) {
  if (event.code !== 'Space' || event.repeat || event.ctrlKey || event.altKey || event.metaKey) return
  if (status.value !== 'speaking' || isCatalogOpen.value || isCartOpen.value) return
  const target = event.target as HTMLElement | null
  if (
    target
    && (
      target.tagName === 'INPUT'
      || target.tagName === 'TEXTAREA'
      || target.tagName === 'SELECT'
      || target.isContentEditable
    )
  ) {
    return
  }
  event.preventDefault()
  stopSpeaking()
}

function retryLastPrompt() {
  const prompt = lastPrompt.value
  errorMessage.value = null
  status.value = 'idle'
  if (prompt) void sendMessage(prompt, false, true)
  else void startHandsFree()
}

onMounted(() => {
  document.documentElement.lang = 'vi-VN'
  try {
    const explicitChoice = window.localStorage.getItem('retail-ai-microphone-explicit-choice') === '1'
    keepExplicitMicrophoneChoice = explicitChoice
    // Honor choices made in the picker. Legacy saved IDs are only hints: some
    // installations persisted a virtual mic that no longer receives room audio.
    selectedMicrophone.value = explicitChoice
      ? window.localStorage.getItem('retail-ai-microphone-device') ?? ''
      : ''
  } catch {
    // The OS default remains available when storage is disabled.
  }
  restoreAssistedCart()
  void bootKiosk()
  if (typeof getManagerNotifierStatus === 'function') {
    void getManagerNotifierStatus()
      .then((info) => {
        if (!isUnmounted) managerNotifyChannel.value = info?.enabled ? (info.primary_channel ?? null) : null
      })
      .catch(() => {
        // Optional status badge; never block kiosk startup.
      })
  }
  if (typeof getPayOSStatus === 'function') {
    void getPayOSStatus()
      .then((info) => {
        if (!isUnmounted) payosEnabled.value = Boolean(info?.enabled)
      })
      .catch(() => {
        // PayOS status is optional; counter checkout stays available.
      })
  }
  catalogPrefetchTimer = setTimeout(() => {
    if (!isUnmounted) prefetchCatalogInBackground()
  }, 1_200)
  const searchParams = new URLSearchParams(window.location.search)
  const payosResult = searchParams.get('payos')
  const returnedOrderCode = Number(searchParams.get('orderCode') || searchParams.get('payosOrderCode') || 0)
  const savedPendingPayOS = readPendingPayOS()
  const payosStatusParam = (searchParams.get('status') || '').toUpperCase()
  const payosCancelled = payosResult === 'cancel' || searchParams.get('cancel') === 'true'
  if (payosCancelled) {
    persistPendingPayOS(null)
    isCartOpen.value = true
    assistedOrderError.value = 'Bạn đã hủy giao dịch PayOS. Giỏ hàng vẫn được giữ nguyên để bạn chọn lại.'
    try {
      window.history.replaceState({}, '', window.location.pathname)
    } catch {
      // Ignore history restrictions.
    }
  } else if (
    (payosResult === 'success' || payosStatusParam === 'PAID')
    && typeof syncPayOSPayment === 'function'
  ) {
    const syncCode = returnedOrderCode > 0 ? returnedOrderCode : (savedPendingPayOS?.payosOrderCode ?? 0)
    if (syncCode > 0) {
      isCartOpen.value = true
      isCheckingPayOS.value = true
      void syncPayOSPayment(syncCode, savedPendingPayOS?.paymentVerifyToken)
        .then((synced) => {
          if (isUnmounted) return
          if (synced.payment_status === 'PAID') {
            finalizePaidPayOSOrder(synced)
          } else if (synced.payment_status === 'PENDING_PAYOS') {
            activeAssistedOrder.value = synced
            persistPendingPayOS(synced)
            startPayOSPolling(synced)
          } else {
            persistPendingPayOS(null)
            assistedOrderError.value = synced.customer_message || 'Giao dịch PayOS chưa hoàn tất.'
          }
        })
        .catch(() => {
          if (!isUnmounted) {
            assistedOrderError.value = 'Chưa xác minh được giao dịch PayOS vừa quay lại. Bạn thử bấm kiểm tra lại nhé.'
          }
        })
        .finally(() => {
          if (!isUnmounted) isCheckingPayOS.value = false
        })
    }
    try {
      window.history.replaceState({}, '', window.location.pathname)
    } catch {
      // Ignore history restrictions.
    }
  }
  void refreshMicrophones()
  navigator.mediaDevices?.addEventListener?.('devicechange', refreshMicrophones)
  window.addEventListener('keydown', handleGlobalKeydown)
})
onUnmounted(() => {
  clearVoiceDiagnostics()
  isUnmounted = true
  voiceTurnEpoch += 1
  activeTurnController?.abort()
  activeTurnController = null
  activeTranscriptionController?.abort()
  activeTranscriptionController = null
  activeStreamStt?.cancel()
  activeStreamStt = null
  const closingSessionId = sessionId.value
  if (sessionExpiryTimer) clearTimeout(sessionExpiryTimer)
  if (cartToastTimer) clearTimeout(cartToastTimer)
  if (catalogPrefetchTimer) clearTimeout(catalogPrefetchTimer)
  stopPayOSPolling()
  navigator.mediaDevices?.removeEventListener?.('devicechange', refreshMicrophones)
  window.removeEventListener('keydown', handleGlobalKeydown)
  voiceController?.stop()
  invalidateSpeechPlayback()
  closeDirectAzureTts()
  if (closingSessionId) void closeSession(closingSessionId).catch(() => {
    // The browser may be navigating away; backend TTL still bounds any session that cannot close.
  })
})
</script>

<template>
  <main
    class="vrm-only-shell"
    :class="{
      'text-is-open': isTextOpen,
      'mobile-sheet-expanded': isMobileSheetExpanded && isTextOpen,
      'modal-is-open': isCatalogOpen || isCartOpen,
    }"
  >
    <header class="mobile-topbar">
      <div class="mobile-brand">
        <span class="mobile-brand-badge">M</span>
        <div class="mobile-brand-copy">
          <strong>Mai</strong>
          <span>Trợ lý siêu thị</span>
        </div>
      </div>
      <div class="mobile-topbar-right">
        <button
          type="button"
          class="mobile-catalog-pill"
          aria-label="Xem tất cả sản phẩm theo danh mục"
          @click="handleOpenCatalog"
        >
          <SquaresFour :size="15" weight="duotone" aria-hidden="true" />
          <span>Sản phẩm</span>
        </button>
        <button
          type="button"
          class="mobile-cart-pill"
          :class="{ 'has-items': assistedTotalCount > 0 }"
          :aria-label="assistedTotalCount > 0 ? `Mở giỏ hàng (${assistedTotalCount} sản phẩm)` : 'Mở giỏ hàng'"
          @click="handleOpenCart(assistedTotalCount === 0 && assistedOrders.length > 0 ? 'orders' : 'cart')"
        >
          <ShoppingBagOpen :size="16" weight="duotone" aria-hidden="true" />
          <span v-if="assistedTotalCount > 0" class="mobile-cart-count">{{ assistedTotalCount }}</span>
          <span v-else-if="assistedOrders.length > 0" class="mobile-cart-count is-orders">{{ assistedOrders.length }}</span>
        </button>
        <div class="mobile-status-pill" :class="`state-${status}`" :title="statusHint">
          <span class="mobile-status-dot"></span>
          <span class="mobile-status-text">{{ statusHint }}</span>
        </div>
      </div>
    </header>
    <nav class="kiosk-corner-actions" aria-label="Xem sản phẩm và giỏ hàng">
      <button
        type="button"
        class="corner-action-btn is-catalog"
        aria-label="Xem tất cả sản phẩm theo danh mục"
        @pointerenter="prefetchCatalogInBackground"
        @click="handleOpenCatalog"
      >
        <SquaresFour :size="18" weight="duotone" aria-hidden="true" />
        <span>Sản phẩm</span>
      </button>
      <button
        type="button"
        class="corner-action-btn is-cart"
        :class="{ 'has-items': assistedTotalCount > 0 }"
        :aria-label="assistedTotalCount > 0 ? `Mở giỏ hàng (${assistedTotalCount} sản phẩm)` : 'Mở giỏ hàng'"
        @click="handleOpenCart(assistedTotalCount === 0 && assistedOrders.length > 0 ? 'orders' : 'cart')"
      >
        <ShoppingBagOpen :size="18" weight="duotone" aria-hidden="true" />
        <span>Giỏ hàng</span>
        <span v-if="assistedTotalCount > 0" class="corner-cart-badge">{{ assistedTotalCount }}</span>
        <span v-else-if="assistedOrders.length > 0" class="corner-cart-badge is-orders">{{ assistedOrders.length }}</span>
      </button>
    </nav>
    <aside v-if="voiceDebugAvailable" class="voice-debug-panel" aria-label="Local voice diagnostics">
      <label><input type="checkbox" :checked="voiceDebugEnabled" @change="setVoiceDebug"> Keep latest voice turn locally</label>
      <template v-if="voiceDiagnostics.audioUrl">
        <audio controls :src="voiceDiagnostics.audioUrl" aria-label="Exact uploaded recording"></audio>
        <a :href="voiceDiagnostics.audioUrl" download="voice-upload">Download audio</a>
        <a :href="voiceDiagnostics.jsonUrl" download="voice-diagnostics.json">Download JSON</a>
        <button type="button" @click="clearVoiceDiagnostics">Clear recording</button>
      </template>
    </aside>
    <section class="vrm-stage" :aria-label="language === 'en' ? 'Mai avatar' : language === 'zh' ? 'Mai 虚拟形象' : 'Avatar Mai'">
      <MaiAvatar
        :state="status"
        :audio-playing="isAudioPlaying"
        :audio-element="audioElement"
        :speech-source="speechSourceMode"
        :speech-boundary-token="speechBoundaryToken"
        :speech-boundary-word="speechBoundaryWord"
        :paused="isCatalogOpen || isCartOpen"
      />

      <div class="voice-feedback" role="status" aria-live="polite">
        <strong>{{ statusHint }}</strong>
        <p>{{ errorMessage || voiceHint }}</p>
        <meter v-if="isVoiceActive" class="microphone-level" min="0" max="1" :value="microphoneLevel" :aria-label="microphoneLevelLabel"></meter>
      </div>

      <div class="vrm-controls" :aria-label="language === 'en' ? 'Mai controls' : language === 'zh' ? 'Mai 控制' : 'Điều khiển Mai'">
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
          :disabled="!isHandsFree && (status === 'transcribing' || status === 'thinking')"
          @click="toggleRecording"
        >
          <Stop v-if="isVoiceActive" :size="22" weight="fill" aria-hidden="true" />
          <Microphone v-else :size="22" weight="duotone" aria-hidden="true" />
          <span>{{ voiceButtonLabel }}</span>
        </button>
        <button
          v-if="status === 'speaking'"
          type="button"
          class="vrm-control vrm-interrupt-control"
          :aria-label="language === 'en' ? 'Stop Mai speaking' : language === 'zh' ? '停止 Mai 说话' : 'Dừng Mai nói'"
          :title="language === 'vi' ? 'Ngắt lời Mai để nói ngay (hoặc nhấn phím Space)' : 'Interrupt Mai (Space)'"
          @click="stopSpeaking"
        >
          <Pause :size="20" weight="fill" aria-hidden="true" />
          <span>{{ language === 'en' ? 'Interrupt' : language === 'zh' ? '打断' : 'Ngắt & nói' }}</span>
        </button>
        <button
          type="button"
          class="vrm-control vrm-camera-control"
          aria-label="Chụp ảnh sản phẩm"
          title="Chụp hoặc gửi ảnh sản phẩm để hỏi Mai"
          @click="openCameraForProduct"
        >
          <Camera :size="22" weight="duotone" aria-hidden="true" />
        </button>
        <button
          type="button"
          class="vrm-control vrm-manager-control"
          :class="{ 'is-live': Boolean(managerNotifyChannel) }"
          aria-label="Gọi quản lý"
          :title="managerNotifyChannel ? `Gọi quản lý ngay (gửi qua ${managerNotifyChannel.toUpperCase()})` : 'Gọi quản lý hỗ trợ'"
          :disabled="status === 'thinking' || status === 'transcribing'"
          @click="handleCallManager"
        >
          <BellRinging :size="21" weight="duotone" aria-hidden="true" />
        </button>
        <button
          type="button"
          class="vrm-control vrm-cart-control"
          :class="{ 'has-items': assistedTotalCount > 0 }"
          :aria-label="assistedTotalCount > 0 ? `Mở giỏ hàng (${assistedTotalCount} sản phẩm)` : 'Mở giỏ hàng'"
          title="Xem giỏ hàng &amp; nhờ nhân viên chọn hàng"
          @click="handleOpenCart(assistedTotalCount === 0 && assistedOrders.length > 0 ? 'orders' : 'cart')"
        >
          <ShoppingBagOpen :size="21" weight="duotone" aria-hidden="true" />
          <span v-if="assistedTotalCount > 0" class="vrm-cart-badge">{{ assistedTotalCount }}</span>
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
          class="mobile-sheet-grabber"
          :aria-label="isMobileSheetExpanded ? 'Thu gọn bảng hội thoại' : 'Mở rộng bảng hội thoại'"
          :aria-pressed="isMobileSheetExpanded"
          @click="toggleMobileSheetSize"
        >
          <span></span>
        </button>
        <button
          type="button"
          class="drawer-close"
          :aria-label="language === 'en' ? 'Close text panel' : language === 'zh' ? '关闭文字面板' : 'Đóng phần chữ'"
          @click="isTextOpen = false; isMobileSheetExpanded = false"
        >
          <X :size="20" weight="bold" aria-hidden="true" />
        </button>
        <ChatPanel
          :messages="messages"
          :cart="cart"
          :checkout-quote="checkoutQuote"
          :order="sandboxOrder"
          :assisted-items="assistedCart"
          :assisted-total-count="assistedTotalCount"
          :assisted-total-vnd="assistedTotalVnd"
          :assisted-orders-count="assistedOrders.length"
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
          :camera-trigger-token="cameraTriggerToken"
          :manager-notify-channel="managerNotifyChannel"
          @update:draft="draft = $event"
          @send="sendMessage()"
          @send-image="handleSendImage"
          @quick-prompt="handleQuickPrompt"
          @toggle-recording="toggleRecording"
          @stop-speaking="stopSpeaking"
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
          @add-assisted-item="handleAddAssistedItem"
          @update-assisted-qty="handleUpdateAssistedQty"
          @open-cart="handleOpenCart"
          @open-catalog="handleOpenCatalog"
        />
      </aside>
    </Transition>

    <Transition name="cart-toast">
      <div v-if="cartToastMessage && !isCartOpen && !isCatalogOpen" class="cart-toast" role="status" aria-live="polite">
        <ShoppingBagOpen :size="16" weight="duotone" aria-hidden="true" />
        <span>{{ cartToastMessage }}</span>
        <button type="button" class="cart-toast-link" @click="handleOpenCart('cart')">Xem giỏ</button>
      </div>
    </Transition>

    <ProductCatalogModal
      :open="isCatalogOpen"
      :assisted-items="assistedCart"
      :assisted-total-count="assistedTotalCount"
      :assisted-total-vnd="assistedTotalVnd"
      :language="language"
      @close="isCatalogOpen = false"
      @add-item="handleAddAssistedItem"
      @update-qty="handleUpdateAssistedQty"
      @open-cart="handleOpenCart('cart')"
      @ask-mai="handleAskMaiFromCatalog"
    />

    <CartDrawer
      :open="isCartOpen"
      :items="assistedCart"
      :orders="assistedOrders"
      :active-order="activeAssistedOrder"
      :initial-tab="cartInitialTab"
      :is-submitting="isSubmittingAssistedOrder"
      :is-checking-payment="isCheckingPayOS"
      :submit-error="assistedOrderError"
      :language="language"
      :manager-notify-channel="managerNotifyChannel"
      :payos-enabled="payosEnabled"
      @close="isCartOpen = false"
      @update-qty="handleUpdateAssistedQty"
      @remove-item="handleRemoveAssistedItem"
      @clear-cart="handleClearAssistedCart"
      @submit-order="handleSubmitAssistedOrder"
      @check-payment="(ord) => handleCheckPayOSPayment(ord, false)"
      @cancel-payment="handleCancelPayOSPayment"
      @dismiss-active-order="activeAssistedOrder = null"
      @browse-suggestions="handleBrowseCartSuggestions"
    />

    <audio ref="audioElement" class="sr-only" preload="auto" aria-hidden="true"></audio>
  </main>
</template>
