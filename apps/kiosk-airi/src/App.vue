<script setup lang="ts">
import {
  PhChatText as ChatText,
  PhMicrophone as Microphone,
  PhPause as Pause,
  PhSpeakerHigh as SpeakerHigh,
  PhStop as Stop,
  PhX as X,
} from '@phosphor-icons/vue'
import {
  computed,
  defineAsyncComponent,
  onMounted,
  onUnmounted,
  ref,
} from 'vue'
import {
  addCartItem,
  cancelCheckout,
  closeSession,
  confirmCheckout,
  createRequestId,
  createSession,
  createSynthesisTicket,
  getCart,
  prepareVoice,
  prepareCheckout,
  respond,
  setCartItemQuantity,
  simulateSandboxPayment,
  transcribe,
} from './api'
import ChatPanel from './components/ChatPanel.vue'
import type { CheckoutQuote, SandboxOrder, ShoppingCart } from './types'
const SkyAvatar = defineAsyncComponent(
  () => import('./components/SkyAvatar.vue'),
)
import {
  listMicrophoneOptions,
  preferredMicrophoneId,
} from './microphone-devices'
import type { AssistantStatus, ChatMessage, TranscriptionResponse } from './types'
import { TranscriptionPreview } from './voice-preview'
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
const microphoneDevices = ref<
  Array<{
    id: string
    label: string
  }>
>([])
const selectedMicrophone = ref('')
const activeMicrophone = ref('')
const isBooting = ref(true)
const isHandsFree = ref(false)
const isTextOpen = ref(false)

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
let voiceEpoch = 0
let transcriptionRequest: AbortController | null = null
const transcriptionPreview = new TranscriptionPreview<TranscriptionResponse>()
let synthesisRequest: AbortController | null = null
let speakerTailTimer: ReturnType<typeof setTimeout> | undefined
let voiceController: VoiceConversationController | null = null
// The same AEC-enabled capture listens for a customer's next turn during TTS.
let isBargeInCapture = false
let listeningStartedAt = 0
let sessionExpiryTimer: ReturnType<typeof setTimeout> | undefined
let sessionIdleTimeoutMs = 30 * 60 * 1000
let microphoneChosen = false
let microphonePreferenceLoaded = false
let microphoneRefreshId = 0
let recognitionNotice: string | null = null
const voiceCopy = {
  label: 'tiếng Việt',
  locale: 'vi-VN',
  switching: 'Đang chuyển sang tiếng Việt',
  ready: 'Nhập câu hỏi hoặc bấm mic để nói',
  listening: 'Mình đang nghe, bạn cứ nói tự nhiên nhé',
  preparing: 'Đang chuẩn bị chế độ hội thoại tiếng Việt',
  thinking: 'Mình đang tìm câu trả lời cho bạn',
  recording: 'Đang ghi âm tiếng Việt',
  transcribing: 'Đang nhận diện tiếng Việt',
  permission:
    'Microphone chưa được cấp quyền. Bạn có thể nhập câu hỏi bằng bàn phím.',
  retry: 'Mình sẽ nghe lại sau một chút',
  sessionError: 'Không kết nối được phiên trò chuyện. Bạn có thể thử lại.',
}
const audioUnlockLabel = computed(() => {
  return 'Bật âm thanh'
})
const audioUnlockHint = computed(() => {
  return 'Bấm một lần để cho phép Sky phát giọng nói.'
})
const textPanelLabel = computed(() => {
  return isTextOpen.value ? 'Đóng phần chữ' : 'Mở phần chữ'
})
const voiceButtonLabel = computed(() => {
  return isHandsFree.value ? 'Dừng nghe' : 'Bắt đầu nói'
})
const statusHint = computed(() => {
  if (status.value === 'listening') return 'Đang nghe'
  if (status.value === 'transcribing') return 'Đang nhận diện'
  if (status.value === 'thinking') return 'Đang xử lý'
  if (status.value === 'speaking') return 'Đang trả lời'
  if (status.value === 'error') return 'Cần thử lại'
  return isBooting.value ? 'Đang khởi tạo' : 'Sẵn sàng'
})
const microphoneLevelLabel = computed(() => 'Mức tín hiệu micro')
function warmVoiceConnection(id: string) {
  void prepareVoice(id).catch(() => {})
}
function toErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}
function pushMessage(
  role: ChatMessage['role'],
  text: string,
  metadata?: Partial<ChatMessage>,
) {
  messages.value.push({
    id: `${role}-${Date.now()}-${messages.value.length}`,
    role,
    text,
    ...metadata,
  })
}
function handleVoiceState(state: VoiceState) {
  isVoiceActive.value = state.mode !== 'idle'
  isRecording.value = state.phase === 'recording'
  if (state.phase === 'listening') {
    listeningStartedAt = Date.now()
    void refreshMicrophones()
    if (status.value === 'speaking') {
      isVoiceActive.value = true
      voiceHint.value = 'Bạn có thể nói chen để Sky dừng lại'
      return
    }
    status.value = 'listening'
    voiceHint.value = recognitionNotice ?? voiceCopy.listening
    return
  }
  if (state.phase === 'recording') {
    recognitionNotice = null
    interruptOnSpeechStart()
    status.value = 'listening'
    voiceHint.value = voiceCopy.recording
    return
  }
  if (
    state.mode === 'idle' &&
    !['thinking', 'transcribing', 'speaking', 'error'].includes(status.value)
  ) {
    status.value = 'idle'
  }
}
function handleVoiceError(error: VoiceControllerError) {
  transcriptionPreview.cancel()
  // A failed interrupt listener must not leave speech playing with its Stop
  // control hidden by the error state.
  if (status.value === 'speaking') {
    ++speechRequestId
    pendingSpeechText.value = null
    releaseAudioPlayback()
    if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  }
  isBargeInCapture = false
  isVoiceActive.value = false
  isHandsFree.value = false
  isRecording.value = false
  microphoneLevel.value = 0
  errorMessage.value =
    error.kind === 'permission' ? voiceCopy.permission : error.message
  voiceHint.value = voiceCopy.retry
  status.value = 'error'
}
async function handleVoiceAudio(audio: Blob, previewId?: number) {
  if (
    !sessionId.value ||
    isUnmounted ||
    !isHandsFree.value ||
    speakerTailTimer ||
    ['transcribing', 'thinking', 'speaking'].includes(status.value)
  )
    return
  const epoch = voiceEpoch
  const preview = transcriptionPreview.take(previewId, sessionId.value)
  pauseVoice()
  const request = preview?.controller ?? new AbortController()
  transcriptionRequest?.abort()
  transcriptionRequest = request
  status.value = 'transcribing'
  voiceHint.value = voiceCopy.transcribing
  try {
    let result = preview ? await preview.result : null
    if (epoch !== voiceEpoch || isUnmounted || request.signal.aborted) return
    // A rejected/failed preview is not a rejection of the full recording.
    if (!result?.speech_detected || !result.text.trim()) {
      result = await withSessionRecovery((id) => transcribe(id, audio, 'vi', request.signal))
    }
    if (epoch !== voiceEpoch || isUnmounted || request.signal.aborted) return
    if (import.meta.env.DEV) console.debug('[STT]', {
      request: result.request_id, pipeline: result.pipeline_version,
      provider: result.provider, model: result.model,
      accepted: result.speech_detected, reason: result.rejection_reason, quality: result.quality,
    })
    if (!result.speech_detected || !result.text.trim()) {
      status.value = 'idle'
      recognitionNotice = result.rejection_reason === 'no_speech'
        ? 'Chưa thu rõ giọng nói. Bạn nói gần mic hơn và thử lại nhé.'
        : 'Mình chưa nhận rõ câu vừa rồi. Bạn nói lại giúp mình nhé.'
      voiceHint.value = recognitionNotice
      void resumeHandsFree()
      return
    }
    await sendMessage(result.text, true)
  } catch (error) {
    if (epoch !== voiceEpoch || isUnmounted || request.signal.aborted) return
    errorMessage.value = toErrorMessage(error, 'Không nhận diện được tiếng nói')
    status.value = 'error'
    voiceHint.value = voiceCopy.retry
    void resumeHandsFree()
  } finally {
    if (transcriptionRequest === request) transcriptionRequest = null
  }
}
function ensureVoiceController(): VoiceConversationController {
  if (!voiceController) {
    voiceController = new VoiceConversationController({
      deviceId: selectedMicrophone.value,
      onState: handleVoiceState,
      onSpeechStart: () => {
        interruptOnSpeechStart()
        if (sessionId.value) warmVoiceConnection(sessionId.value)
      },
      onAudioPreview: (audio, id) => {
        if (!sessionId.value || !isHandsFree.value || status.value !== 'listening') return
        const session = sessionId.value
        transcriptionPreview.start(id, session, signal => transcribe(session, audio, 'vi', signal))
      },
      onAudioPreviewInvalidated: id => transcriptionPreview.cancel(id),
      onAudio: (audio, previewId) => {
        void handleVoiceAudio(audio, previewId)
      },
      onLevel: (level) => {
        microphoneLevel.value = level
        if (
          isVoiceActive.value &&
          !isRecording.value && status.value === 'listening' &&
          Date.now() - listeningStartedAt > 8000
        ) {
          voiceHint.value =
            'Chưa nhận được câu nói. Bạn nói gần mic hoặc kiểm tra micro đầu vào nhé.'
        }
      },
      onInputDevice: (label) => {
        activeMicrophone.value = label
      },
      onNotice: (message) => {
        if (status.value === 'speaking') return
        recognitionNotice = message
        voiceHint.value = message
      },
      onError: handleVoiceError,
    })
  }
  return voiceController
}
async function refreshMicrophones() {
  const refreshId = ++microphoneRefreshId
  try {
    const devices = await navigator.mediaDevices?.enumerateDevices()
    if (isUnmounted || refreshId !== microphoneRefreshId) return
    // Browsers hide physical labels until microphone permission is granted.
    // Wait for the first capture/listening event before choosing a device.
    if (
      !devices?.some(
        (device) => device.kind === 'audioinput' && device.label.trim(),
      )
    )
      return
    const options = listMicrophoneOptions(devices ?? [])
    microphoneDevices.value = options
    let savedId = ''
    try {
      const saved = window.localStorage.getItem('retail-ai-microphone-device')
      savedId = saved ?? ''
      if (!microphonePreferenceLoaded && saved !== null) {
        selectedMicrophone.value = saved
        microphoneChosen = true
      }
    } catch {
      // Storage can be unavailable in private browsing; device selection still works in memory.
    }
    microphonePreferenceLoaded = true
    const preferredId = preferredMicrophoneId(
      options,
      selectedMicrophone.value,
      savedId,
      microphoneChosen,
    )
    if (!preferredId) return
    selectedMicrophone.value = preferredId
    voiceController?.setInputDevice(preferredId)
    try {
      window.localStorage.setItem('retail-ai-microphone-device', preferredId)
    } catch {
      // Keep the selection for this page even when it cannot be persisted.
    }
    // Automatic discovery only selects the next capture's input. Restarting
    // here discards audio already buffered by VAD, including a spoken prefix.
  } catch {
    // Capture can still work when the browser restricts device enumeration.
  }
}
async function changeMicrophone(deviceId: string) {
  ++microphoneRefreshId
  microphoneChosen = true
  microphonePreferenceLoaded = true
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
  if (
    isUnmounted ||
    speakerTailTimer ||
    ['transcribing', 'thinking', 'speaking'].includes(status.value)
  )
    return
  if (!sessionId.value) await bootKiosk()
  if (!sessionId.value || isUnmounted) return
  // Voice turns need visible feedback: show listening state, transcript and any errors.
  isTextOpen.value = true
  unlockAudio()
  isHandsFree.value = true
  warmVoiceConnection(sessionId.value)
  voiceHint.value =
    'Đang mở micro. Hãy cho phép dùng micro nếu trình duyệt hỏi.'
  try {
    await ensureVoiceController().start()
  } catch (error) {
    handleVoiceError({
      kind: 'runtime',
      message: toErrorMessage(error, 'Không mở được chế độ hội thoại.'),
      cause: error,
    })
  }
}
async function resumeHandsFree() {
  if (isHandsFree.value) await startHandsFree()
}
function pauseVoice() {
  transcriptionPreview.cancel()
  voiceController?.pause()
  isBargeInCapture = false
  isVoiceActive.value = false
  isRecording.value = false
  microphoneLevel.value = 0
}
function cancelVoiceTurn() {
  transcriptionPreview.cancel()
  recognitionNotice = null
  voiceEpoch += 1
  transcriptionRequest?.abort()
  transcriptionRequest = null
  clearTimeout(speakerTailTimer)
  speakerTailTimer = undefined
}
function finishSpeech(requestId: number, afterSpeech?: () => void) {
  if (requestId !== speechRequestId || isUnmounted) return
  isBargeInCapture = false
  // A warm listener preserves pre-roll and any speech starting at the end of
  // TTS. Reopening the input here used to create a dead zone on every turn.
  if (isHandsFree.value && (voiceController?.isActive || isVoiceActive.value)) {
    status.value = 'listening'
    voiceHint.value = voiceCopy.listening
    return
  }
  status.value = 'idle'
  voiceHint.value = 'Đang mở lại micro. Chờ báo đang nghe rồi nói nhé.'
  clearTimeout(speakerTailTimer)
  // Let speaker echo decay before opening a fresh input stream.
  speakerTailTimer = setTimeout(() => {
    speakerTailTimer = undefined
    if (requestId === speechRequestId && !isUnmounted) afterSpeech?.()
  }, 400)
}
function interruptOnSpeechStart() {
  if (!isBargeInCapture || status.value !== 'speaking' || !isHandsFree.value) return
  isBargeInCapture = false
  ++speechRequestId
  pendingSpeechText.value = null
  needsAudioUnlock.value = false
  releaseAudioPlayback()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  // Keep the VAD buffer, including the leading 640ms, for the new user turn.
  status.value = 'listening'
  voiceHint.value = 'Mình đang nghe câu mới của bạn'
}
function unlockAudio(): Promise<boolean> {
  if ('speechSynthesis' in window) window.speechSynthesis.resume?.()
  if (audioUnlocked.value) return Promise.resolve(true)
  if (audioUnlockPromise) return audioUnlockPromise
  const existingAudio = audioPlayback
  const audio =
    existingAudio ??
    audioElement.value ??
    (typeof Audio === 'undefined' ? null : new Audio())
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
  return (
    'name' in error &&
    (
      error as {
        name?: string
      }
    ).name === 'NotAllowedError'
  )
}
function releaseAudioPlayback() {
  synthesisRequest?.abort()
  synthesisRequest = null
  if (audioPlayback) {
    audioPlayback.onplay = null
    audioPlayback.onended = null
    audioPlayback.onerror = null
    audioPlayback.pause()
    audioPlayback.removeAttribute('src')
    // Cancel the media fetch as well as playback when a turn is interrupted.
    audioPlayback.load()
    audioPlayback = null
  }
}
function speakWithBrowser(
  text: string,
  afterSpeech?: () => void,
  requestId = speechRequestId,
): boolean {
  if (
    !('speechSynthesis' in window) ||
    typeof SpeechSynthesisUtterance === 'undefined'
  ) {
    finishSpeech(requestId, afterSpeech)
    return false
  }
  const synthesis = window.speechSynthesis
  synthesis.cancel()
  synthesis.resume?.()
  pauseVoice()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = voiceCopy.locale
  utterance.rate = 0.88
  utterance.pitch = 1.02
  const preferredLanguage = voiceCopy.locale.slice(0, 2).toLowerCase()
  const preferredVoice = synthesis
    .getVoices()
    .find((voice) => voice.lang.toLowerCase().startsWith(preferredLanguage))
  if (preferredVoice) utterance.voice = preferredVoice
  utterance.onstart = () => {
    if (requestId === speechRequestId && !isUnmounted) status.value = 'speaking'
  }
  const finish = () => {
    if (requestId !== speechRequestId || isUnmounted) return
    finishSpeech(requestId, afterSpeech)
  }
  utterance.onend = finish
  utterance.onerror = finish
  status.value = 'speaking'
  synthesis.speak(utterance)
  void startBargeInCapture()
  return true
}
async function startBargeInCapture() {
  if (
    !isHandsFree.value ||
    isUnmounted ||
    !sessionId.value ||
    status.value !== 'speaking' ||
    !voiceController
  ) return
  isBargeInCapture = true
  try {
    await voiceController.start()
  } catch {
    // Normal voice errors are reported by the capture controller.
    isBargeInCapture = false
  }
}
async function speak(text: string, afterSpeech?: () => void) {
  const requestId = ++speechRequestId
  clearTimeout(speakerTailTimer)
  speakerTailTimer = undefined
  pauseVoice()
  releaseAudioPlayback()
  const request = new AbortController()
  synthesisRequest = request
  status.value = 'speaking'
  voiceHint.value = 'Mình đang trả lời bằng giọng nói'
  // Open before requesting audio, so speech can interrupt TTS startup too.
  void startBargeInCapture()
  try {
    if (!sessionId.value || typeof Audio === 'undefined') {
      throw new Error('Cloud audio playback is unavailable')
    }
    const audioUrl = await withSessionRecovery((id) =>
      createSynthesisTicket(id, text, 'vi', request.signal),
    )
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
      finishSpeech(requestId, afterSpeech)
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
      if (requestId !== speechRequestId || isUnmounted) return
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
  isBooting.value = true
  voiceHint.value = voiceCopy.preparing
  try {
    const session = await createSession('kiosk-01')
    if (isUnmounted) return
    sessionId.value = session.session_id
    sessionIdleTimeoutMs = Math.max(
      1000,
      new Date(session.expires_at).getTime() - Date.now(),
    )
    scheduleSessionExpiry(session.expires_at)
    try {
      cart.value = await getCart(session.session_id)
    } catch {
      // Catalog configuration can disable sandbox carts while text lookup is still available.
      cart.value = null
    }
    voiceHint.value = voiceCopy.ready
  } catch (error) {
    errorMessage.value = toErrorMessage(error, 'Không mở được phiên kiosk')
    status.value = 'error'
    voiceHint.value = voiceCopy.sessionError
  } finally {
    isBooting.value = false
  }
}
function scheduleSessionExpiry(expiresAt: string) {
  if (sessionExpiryTimer) clearTimeout(sessionExpiryTimer)
  const expiryTimestamp = new Date(expiresAt).getTime()
  if (!Number.isFinite(expiryTimestamp)) return
  const delay = Math.max(0, expiryTimestamp - Date.now())
  sessionExpiryTimer = setTimeout(() => {
    void resetKioskSession(
      'Phiên mua sắm đã hết hạn. Mình đã xóa nội dung và mở phiên mới.',
    )
  }, delay)
}
async function resetKioskSession(notice?: string) {
  cancelVoiceTurn()
  const oldSessionId = sessionId.value
  if (sessionExpiryTimer) clearTimeout(sessionExpiryTimer)
  sessionExpiryTimer = undefined
  speechRequestId += 1
  pendingSpeechText.value = null
  pauseVoice()
  releaseAudioPlayback()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  sessionId.value = null
  messages.value = []
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
  if (oldSessionId) {
    try {
      await closeSession(oldSessionId)
    } catch {
      // The server may already have expired or closed this session.
    }
  }
  await bootKiosk()
  if (notice) voiceHint.value = notice
}
async function withSessionRecovery<T>(
  action: (id: string) => Promise<T>,
  retryAfterSessionLoss = false,
): Promise<T> {
  if (!sessionId.value) throw new Error('Chưa kết nối được phiên trò chuyện.')
  const activeSessionId = sessionId.value
  try {
    const result = await action(activeSessionId)
    if (sessionId.value === activeSessionId) {
      scheduleSessionExpiry(
        new Date(Date.now() + sessionIdleTimeoutMs).toISOString(),
      )
    }
    return result
  } catch (error) {
    const failure = error as {
      status?: number
      message?: string
    }
    const sessionGone = /session (?:is )?(?:closed|expired|not found)/i.test(
      failure?.message ?? '',
    )
    if (![404, 409, 410].includes(failure?.status ?? 0) || !sessionGone)
      throw error
    await resetKioskSession(
      'Phiên trước đã kết thúc. Mình đã xóa hội thoại; bạn nói lại yêu cầu trong phiên mới nhé.',
    )
    if (retryAfterSessionLoss && sessionId.value) {
      const resumedSessionId = sessionId.value
      const result = await action(resumedSessionId)
      if (sessionId.value === resumedSessionId) {
        scheduleSessionExpiry(
          new Date(Date.now() + sessionIdleTimeoutMs).toISOString(),
        )
      }
      return result
    }
    throw new Error(
      'Phiên trước đã kết thúc. Hội thoại cũ đã được xóa; vui lòng nói lại yêu cầu.',
    )
  }
}
async function sendMessage(value = draft.value, fromVoice = false) {
  const text = value.trim()
  if (
    !text ||
    status.value === 'thinking' ||
    (status.value === 'transcribing' && !fromVoice)
  )
    return
  if (!sessionId.value) await bootKiosk()
  if (!sessionId.value || isUnmounted) return
  const epoch = voiceEpoch
  // Retire all playback callbacks before a new chat turn can change state.
  speechRequestId += 1
  clearTimeout(speakerTailTimer)
  speakerTailTimer = undefined
  pendingSpeechText.value = null
  releaseAudioPlayback()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  if (fromVoice) void unlockAudio()
  pauseVoice()
  draft.value = ''
  lastPrompt.value = text
  errorMessage.value = null
  pushMessage('user', text)
  status.value = 'thinking'
  voiceHint.value = voiceCopy.thinking
  try {
    const clientTurnId = createRequestId()
    const response = await withSessionRecovery((id) =>
      respond(id, text, 'vi', clientTurnId),
    )
    if (epoch !== voiceEpoch || isUnmounted) return
    if (response.state === 'CLOSED') {
      await resetKioskSession(
        'Phiên đã kết thúc. Nội dung cũ đã được xóa khỏi kiosk.',
      )
      return
    }
    pushMessage('assistant', response.text, {
      model: response.model,
      groundedProductIds: response.grounded_product_ids,
      products: response.products,
      supportTicket: response.support_ticket ?? undefined,
    })
    if (response.cart) cart.value = response.cart
    if (response.checkout_quote) checkoutQuote.value = response.checkout_quote
    if (response.order) sandboxOrder.value = response.order
    if (fromVoice) {
      void speak(response.text, resumeHandsFree)
    } else {
      status.value = 'idle'
      voiceHint.value = voiceCopy.ready
      void resumeHandsFree()
    }
  } catch (error) {
    if (epoch !== voiceEpoch || isUnmounted) return
    errorMessage.value = toErrorMessage(
      error,
      'Không nhận được câu trả lời từ trợ lý',
    )
    voiceHint.value = 'Bạn thử lại câu hỏi vừa rồi nhé'
    status.value = 'error'
  }
}
function handleQuickPrompt(value: string) {
  void sendMessage(value)
}
function showShoppingError(error: unknown) {
  errorMessage.value = toErrorMessage(
    error,
    'Chưa cập nhật được giỏ demo. Bạn thử lại nhé.',
  )
  status.value = 'error'
}
async function handleAddProduct(productId: string) {
  if (!sessionId.value) return
  try {
    cart.value = await withSessionRecovery((id) =>
      addCartItem(id, productId, 1, createRequestId()),
    )
    checkoutQuote.value = null
    sandboxOrder.value = null
    status.value = 'idle'
    pushMessage(
      'assistant',
      'Mình đã thêm sản phẩm vào giỏ demo. Bạn có thể xem lại số lượng hoặc bắt đầu checkout.',
    )
  } catch (error) {
    showShoppingError(error)
  }
}
async function handleSetCartQuantity(productId: string, quantity: number) {
  if (!sessionId.value) return
  try {
    cart.value = await withSessionRecovery((id) =>
      setCartItemQuantity(id, productId, quantity, createRequestId()),
    )
    checkoutQuote.value = null
    sandboxOrder.value = null
  } catch (error) {
    showShoppingError(error)
  }
}
async function handlePrepareCheckout() {
  if (!sessionId.value) return
  try {
    checkoutQuote.value = await withSessionRecovery((id) => prepareCheckout(id))
    cart.value = checkoutQuote.value.cart
    sandboxOrder.value = null
  } catch (error) {
    showShoppingError(error)
  }
}
async function handleConfirmCheckout() {
  if (!sessionId.value || !checkoutQuote.value) return
  try {
    const quote = checkoutQuote.value
    sandboxOrder.value = await withSessionRecovery((id) =>
      confirmCheckout(id, quote.confirmation_token, createRequestId()),
    )
    checkoutQuote.value = null
  } catch (error) {
    showShoppingError(error)
  }
}
async function handleCancelCheckout() {
  if (!sessionId.value) return
  try {
    cart.value = await withSessionRecovery((id) => cancelCheckout(id))
    checkoutQuote.value = null
  } catch (error) {
    showShoppingError(error)
  }
}
async function handleSandboxPayment(outcome: 'PAID' | 'FAILED') {
  if (!sessionId.value || !sandboxOrder.value) return
  try {
    const order = await withSessionRecovery((id) =>
      simulateSandboxPayment(
        id,
        sandboxOrder.value!.order_id,
        outcome,
        createRequestId(),
      ),
    )
    if (order.payment_status === 'PAID') {
      await resetKioskSession(
        'Thanh toán mô phỏng hoàn tất; không phát sinh giao dịch thật. Kiosk đã mở phiên mới.',
      )
      return
    }
    sandboxOrder.value = order
    cart.value = await withSessionRecovery((id) => getCart(id))
  } catch (error) {
    showShoppingError(error)
  }
}
function clearSandboxOrder() {
  sandboxOrder.value = null
}
function handleEndSession() {
  void resetKioskSession(
    'Phiên đã kết thúc. Nội dung cũ đã được xóa khỏi kiosk.',
  )
}
function handleRequestSupport() {
  void sendMessage('Mình muốn gặp nhân viên.')
}
function toggleTextPanel() {
  isTextOpen.value = !isTextOpen.value
}
function toggleRecording() {
  unlockAudio()
  if (isHandsFree.value || voiceController?.isActive || isVoiceActive.value) {
    cancelVoiceTurn()
    isHandsFree.value = false
    speechRequestId += 1
    pendingSpeechText.value = null
    releaseAudioPlayback()
    if ('speechSynthesis' in window) window.speechSynthesis.cancel()
    voiceController?.stopCapture()
    isVoiceActive.value = false
    isRecording.value = false
    voiceHint.value = voiceCopy.ready
    status.value = 'idle'
    return
  }
  errorMessage.value = null
  void startHandsFree()
}
function stopSpeaking() {
  speechRequestId += 1
  isBargeInCapture = false
  pendingSpeechText.value = null
  releaseAudioPlayback()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  finishSpeech(speechRequestId, resumeHandsFree)
}
function retryLastPrompt() {
  const prompt = lastPrompt.value
  errorMessage.value = null
  status.value = 'idle'
  if (prompt) void sendMessage(prompt)
  else void startHandsFree()
}

onMounted(() => {
  void bootKiosk()
  void refreshMicrophones()
  navigator.mediaDevices?.addEventListener?.('devicechange', refreshMicrophones)
})
onUnmounted(() => {
  isUnmounted = true
  cancelVoiceTurn()
  if (sessionExpiryTimer) clearTimeout(sessionExpiryTimer)
  navigator.mediaDevices?.removeEventListener?.(
    'devicechange',
    refreshMicrophones,
  )
  voiceController?.stop()
  speechRequestId += 1
  releaseAudioPlayback()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
})
</script>

<template>
  <main class="vrm-only-shell" :class="{ 'text-is-open': isTextOpen }">
    <section class="vrm-stage" aria-label="Avatar Sky">
      <header class="vrm-brand-bar">
        <div class="vrm-brand-mark" aria-hidden="true">S</div>
        <div class="vrm-brand-copy">
          <strong>Sky</strong>
          <span>Trợ lý mua sắm</span>
        </div>
        <div class="vrm-live-pill" :class="`is-${status}`">
          <i aria-hidden="true"></i>
          <span>{{ statusHint }}</span>
        </div>
      </header>

      <SkyAvatar src="/avatars/sky.glb" :state="status" />

      <div v-if="messages.length === 0 && !isTextOpen" class="vrm-welcome">
        <p class="vrm-welcome-kicker">Xin chào, mình là Sky</p>
        <h1>Bạn đang tìm gì hôm nay?</h1>
        <p>Nói tự nhiên hoặc mở khung chat để xem câu trả lời và sản phẩm.</p>
      </div>

      <div class="voice-feedback" role="status" aria-live="polite">
        <strong>{{ statusHint }}</strong>
        <p>{{ errorMessage || voiceHint }}</p>
        <meter
          v-if="isVoiceActive"
          class="microphone-level"
          min="0"
          max="1"
          :value="microphoneLevel"
          :aria-label="microphoneLevelLabel"
        ></meter>
      </div>

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
          :disabled="
            !isHandsFree && (status === 'transcribing' || status === 'thinking')
          "
          @click="toggleRecording"
        >
          <Stop
            v-if="isVoiceActive"
            :size="22"
            weight="fill"
            aria-hidden="true"
          />
          <Microphone v-else :size="22" weight="duotone" aria-hidden="true" />
          <span>{{ voiceButtonLabel }}</span>
        </button>
        <button
          v-if="status === 'speaking'"
          type="button"
          class="vrm-control"
          aria-label="Dừng Sky nói"
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
      <aside
        v-if="isTextOpen"
        class="text-drawer"
        aria-label="Bảng hội thoại dạng chữ"
      >
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
          :cart="cart"
          :checkout-quote="checkoutQuote"
          :order="sandboxOrder"
          :status="status"
          :draft="draft"
          :is-recording="isRecording"
          :is-voice-active="isVoiceActive"
          :is-hands-free="isHandsFree"
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

    <audio
      ref="audioElement"
      class="sr-only"
      preload="auto"
      aria-hidden="true"
    ></audio>
  </main>
</template>
