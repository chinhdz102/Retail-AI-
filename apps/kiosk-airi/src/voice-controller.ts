import { SpeechEnvelopeGate } from './voice-speech-gate'
import { PcmCaptureBuffer } from './voice-pcm'
import { createLocalNeuralVad, type NeuralVadCallbacks, type NeuralVadSession } from './voice-neural'
import { createBrowserGtcrnDenoiser, type BrowserSpeechDenoiser } from './voice-gtcrn-denoiser'

export type VoiceMode = 'idle' | 'recognition' | 'recorder'
export type VoicePhase = 'idle' | 'listening' | 'recording'
export type VoicePermission = 'unknown' | 'granted' | 'denied'
export type VoiceErrorKind = 'permission' | 'unsupported' | 'runtime'

export interface VoiceState {
  mode: VoiceMode
  phase: VoicePhase
  permission: VoicePermission
}

export interface VoiceControllerError {
  kind: VoiceErrorKind
  message: string
  cause?: unknown
}

export interface SpeechRecognitionAlternativeLike {
  transcript: string
}

export interface SpeechRecognitionResultLike {
  isFinal: boolean
  length?: number
  [index: number]: SpeechRecognitionAlternativeLike
}

export interface SpeechRecognitionEventLike {
  resultIndex?: number
  results: {
    length: number
    [index: number]: SpeechRecognitionResultLike
  }
}

export interface SpeechRecognitionErrorEventLike {
  error?: string
  message?: string
}

export interface SpeechRecognitionLike {
  lang: string
  continuous: boolean
  interimResults: boolean
  maxAlternatives: number
  onstart: (() => void) | null
  onresult: ((event: SpeechRecognitionEventLike) => void) | null
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
  abort?: () => void
}

export interface SpeechRecognitionConstructor {
  new (): SpeechRecognitionLike
}

export interface MediaStreamTrackLike {
  stop: () => void
  label?: string
  getSettings?: () => MediaTrackSettings
}

export interface VoiceCaptureMetadata {
  source: 'silero-v6' | 'pcm' | 'mediarecorder'
  settings: Record<string, number | boolean | string>
}

/** Do not export deviceId/groupId or device labels with a diagnostic recording. */
export function captureMetadata(stream: MediaStreamLike | null, source: VoiceCaptureMetadata['source']): VoiceCaptureMetadata {
  const settings: VoiceCaptureMetadata['settings'] = {}
  try {
    const actual = stream?.getTracks()[0]?.getSettings?.() as Record<string, unknown> | undefined
    for (const key of ['sampleRate', 'sampleSize', 'channelCount', 'echoCancellation', 'noiseSuppression', 'autoGainControl', 'voiceIsolation', 'latency']) {
      const value = actual?.[key]
      if (typeof value === 'boolean' || typeof value === 'string'
        || (typeof value === 'number' && Number.isFinite(value))) settings[key] = value
    }
  } catch { /* Settings are best-effort; capturing audio must still work. */ }
  return { source, settings }
}

export interface MediaStreamLike {
  getTracks: () => MediaStreamTrackLike[]
}

export interface VoiceMediaConstraints {
  audio: boolean | {
    echoCancellation?: boolean
    noiseSuppression?: boolean
    autoGainControl?: boolean
    /** Prefer a mono voice path; browsers ignore unsupported optional hints. */
    channelCount?: { ideal: number }
    sampleRate?: { ideal: number }
    sampleSize?: { ideal: number }
    /** Chromium/Safari voice isolation when the input device exposes it. */
    voiceIsolation?: boolean
    deviceId?: { exact: string }
  }
}

export interface MediaRecorderDataEventLike {
  data: Blob
}

export interface MediaRecorderLike {
  state: 'inactive' | 'recording' | 'paused'
  mimeType?: string
  ondataavailable: ((event: MediaRecorderDataEventLike) => void) | null
  onstop: (() => void) | null
  onerror: ((event: { error?: string }) => void) | null
  start: (timeslice?: number) => void
  stop: () => void
}

export interface MediaRecorderConstructor {
  new (stream: MediaStreamLike, options?: { mimeType?: string }): MediaRecorderLike
  isTypeSupported?: (mimeType: string) => boolean
}

export interface AudioAnalyserLike {
  fftSize: number
  getByteTimeDomainData: (data: Uint8Array) => void
}

export interface AudioMediaStreamSourceLike {
  connect: (node: AudioAnalyserLike | PcmWorkletNodeLike) => void
  disconnect?: () => void
}

export interface PcmWorkletNodeLike {
  port: { onmessage: ((event: { data: Float32Array }) => void) | null }
  connect: (destination: unknown) => void
  disconnect: () => void
}

export interface AudioContextLike {
  state?: string
  sampleRate?: number
  destination?: unknown
  audioWorklet?: { addModule: (url: string) => Promise<void> }
  resume?: () => Promise<void> | void
  createMediaStreamSource: (stream: MediaStreamLike) => AudioMediaStreamSourceLike
  createAnalyser: () => AudioAnalyserLike
  close: () => Promise<void> | void
}

export interface VoiceControllerDependencies {
  getRecognition?: () => SpeechRecognitionConstructor | null
  createRecognition?: () => SpeechRecognitionLike
  getUserMedia?: (constraints: VoiceMediaConstraints) => Promise<MediaStreamLike>
  MediaRecorder?: MediaRecorderConstructor | null
  createAudioContext?: () => AudioContextLike
  createPcmWorkletNode?: (context: AudioContextLike) => PcmWorkletNodeLike
  createNeuralVad?: (callbacks: NeuralVadCallbacks) => Promise<NeuralVadSession>
  createDenoiser?: () => Promise<BrowserSpeechDenoiser | null>
  setTimeout?: (handler: () => void, timeout: number) => number
  clearTimeout?: (timer: number) => void
  now?: () => number
}

export interface VoiceConversationOptions extends VoiceControllerDependencies {
  locale: string
  deviceId?: string
  preferRecorder?: boolean
  /** Conservative guard against constant noise before uploading speech. */
  validateSpeechEnvelope?: boolean
  /** Browser AudioWorklet PCM capture with 350ms pre-roll; falls back to MediaRecorder. */
  preferPcmWorklet?: boolean
  /** Prefer local Silero VAD before the RMS fallback (opt-in per deployment). */
  preferNeuralVad?: boolean
  /**
   * When true, never interrupt assistant speech from raw VAD/RMS spikes alone
   * (prevents laptop cooling fans / airflow from cutting off TTS). Instead,
   * emit verified candidate audio via `onBargeInCandidateAudio` so real STT
   * confirms spoken human words before interrupting.
   */
  requireSttForBargeIn?: boolean
  /** Optional real-time loudspeaker output RMS (0..1) while kiosk TTS is playing. */
  getPlaybackLevel?: () => number
  /**
   * Optional semantic/prosodic end-of-turn hook (e.g. Smart Turn v3 + Vietnamese
   * clause completeness). Return >0 milliseconds to hold capture open for a
   * mid-sentence continuation before emitting final audio, or 0 to submit now.
   */
  shouldHoldTurnEnd?: () => number | Promise<number>
  onState: (state: VoiceState) => void
  onBargeIn?: () => void
  onBargeInCandidateAudio?: (audio: Blob) => void
  onTranscript: (text: string) => void
  onAudio: (audio: Blob) => void
  onSpeechStartFrame?: (preRollFrames: Float32Array[]) => void
  onSpeechFrame?: (frame: Float32Array) => void
  onSpeechMisfire?: () => void
  onCapture?: (metadata: VoiceCaptureMetadata) => void
  onLevel?: (level: number) => void
  onInputDevice?: (label: string) => void
  onError: (error: VoiceControllerError) => void
}

/** MediaRecorder-compatible adapter to reuse the existing segment/state logic. */
class PcmRecorderAdapter implements MediaRecorderLike {
  state: 'inactive' | 'recording' = 'inactive'
  readonly mimeType = 'audio/wav'
  ondataavailable: ((event: MediaRecorderDataEventLike) => void) | null = null
  onstop: (() => void) | null = null
  onerror: ((event: { error?: string }) => void) | null = null

  private readonly buffer: PcmCaptureBuffer

  constructor(buffer: PcmCaptureBuffer) {
    this.buffer = buffer
  }

  start(): void {
    this.buffer.start()
    this.state = 'recording'
  }

  stop(): void {
    if (this.state === 'inactive') return
    this.state = 'inactive'
    const data = this.buffer.finish()
    this.ondataavailable?.({ data })
    this.onstop?.()
  }
}

const MIN_SPEECH_THRESHOLD = 0.012
const MAX_SPEECH_THRESHOLD = 0.12
const NOISE_FLOOR_ALPHA = 0.08
const SILENCE_DURATION_MS = 600
const MAX_SEGMENT_DURATION_MS = 12_000
const MIN_VOICED_DURATION_MS = 120
const POLL_INTERVAL_MS = 40
const RECOGNITION_START_TIMEOUT_MS = 500
const BARGE_IN_GRACE_MS = 450
const BARGE_IN_NEURAL_MIN_RMS = 0.055
const BARGE_IN_NEURAL_IMMEDIATE_RMS = 0.115
const BARGE_IN_NEURAL_MIN_PROB = 0.74
const BARGE_IN_NEURAL_SUSTAINED_FRAMES = 4
const BARGE_IN_NEURAL_ACTIVE_SPEAKER_FRAMES = 5
const BARGE_IN_SPEAKER_ACTIVE_ENVELOPE = 0.02
const BARGE_IN_POST_CONFIRM_TAIL_FRAMES = 4
const BARGE_IN_POST_CONFIRM_MIN_OBSERVED = 8
const BARGE_IN_POST_CONFIRM_MIN_VOICED = 3
const BARGE_IN_POST_DISARM_MARGIN_MS = 120
const BARGE_IN_POST_DISARM_VOICED_FRAMES = 5
const BARGE_IN_FALLBACK_MIN_RMS = 0.055
const BARGE_IN_FALLBACK_CONFIRM_MS = 220
const NEURAL_MIN_SPEECH_RMS = 0.018
const NEURAL_NOISE_FLOOR_MULTIPLIER = 2.0
const NEURAL_NOISE_FLOOR_ALPHA = 0.06
const NEURAL_ONSET_MIN_VOICED_FRAMES = 4
const NEURAL_ONSET_MIN_CONSECUTIVE_FRAMES = 3
const NEURAL_END_MIN_VOICED_FRAMES = 5
const NEURAL_MAX_ZCR_FOR_VOICED = 0.42
const NEURAL_UNCONFIRMED_STALL_MS = 2_500
const MAX_TURN_CONTINUATION_HOLDS = 2
const MICROPHONE_CONSTRAINTS: VoiceMediaConstraints = {
  audio: {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
    channelCount: { ideal: 1 },
    sampleRate: { ideal: 48_000 },
    sampleSize: { ideal: 16 },
    voiceIsolation: true,
  },
}

interface BrowserWindowLike {
  SpeechRecognition?: SpeechRecognitionConstructor
  webkitSpeechRecognition?: SpeechRecognitionConstructor
  MediaRecorder?: MediaRecorderConstructor
  AudioContext?: new () => AudioContextLike
  webkitAudioContext?: new () => AudioContextLike
}

function browserWindow(): BrowserWindowLike {
  return globalThis as unknown as BrowserWindowLike
}

function defaultDependencies(): VoiceControllerDependencies {
  const currentWindow = browserWindow()
  const mediaDevices = (globalThis as typeof globalThis & {
    navigator?: { mediaDevices?: { getUserMedia?: (constraints: { audio: true }) => Promise<MediaStreamLike> } }
  }).navigator?.mediaDevices
  const getUserMedia = mediaDevices?.getUserMedia
    ? (constraints: VoiceMediaConstraints) => mediaDevices.getUserMedia!(constraints)
    : undefined
  const audioContextConstructor = currentWindow.AudioContext ?? currentWindow.webkitAudioContext

  return {
    getRecognition: () => currentWindow.SpeechRecognition ?? currentWindow.webkitSpeechRecognition ?? null,
    getUserMedia,
    MediaRecorder: currentWindow.MediaRecorder ?? null,
    createAudioContext: audioContextConstructor ? () => new audioContextConstructor() : undefined,
    setTimeout: (handler, timeout) => setTimeout(handler, timeout) as unknown as number,
    clearTimeout: (timer) => clearTimeout(timer),
    now: () => Date.now(),
  }
}

function finalTranscript(event: SpeechRecognitionEventLike): string {
  const startIndex = event.resultIndex ?? 0
  const parts: string[] = []
  for (let index = startIndex; index < event.results.length; index += 1) {
    const result = event.results[index]
    if (result?.isFinal && result[0]?.transcript) parts.push(result[0].transcript)
  }
  return parts.join(' ').trim()
}

function rms(data: Uint8Array): number {
  let sum = 0
  for (const sample of data) {
    const normalized = (sample - 128) / 128
    sum += normalized * normalized
  }
  return Math.sqrt(sum / data.length)
}

function frameZeroCrossingRate(frame: Float32Array): number {
  if (frame.length < 2) return 0
  let crossings = 0
  let prev = frame[0]
  for (let i = 1; i < frame.length; i += 1) {
    const cur = frame[i]
    if ((prev >= 0 && cur < 0) || (prev < 0 && cur >= 0)) crossings += 1
    prev = cur
  }
  return crossings / (frame.length - 1)
}

function mergeFloat32Chunks(chunks: Float32Array[]): Float32Array {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0)
  const out = new Float32Array(total)
  let offset = 0
  for (const chunk of chunks) {
    out.set(chunk, offset)
    offset += chunk.length
  }
  return out
}

interface HighPassStreamHandle {
  stream: MediaStreamLike
  close: () => void
}

/**
 * Strip <90 Hz AC hum, laptop fan rumble, and desk bumps before Silero VAD
 * sees the microphone stream. Gracefully passes through raw `stream` when
 * Web Audio MediaStreamDestination is unavailable (tests / restricted browsers).
 */
function buildPreVadHighPassStream(rawStream: MediaStreamLike): HighPassStreamHandle | null {
  if (typeof globalThis === 'undefined') return null
  const host = globalThis as unknown as {
    AudioContext?: typeof AudioContext
    webkitAudioContext?: typeof AudioContext
  }
  const AudioCtx = host.AudioContext ?? host.webkitAudioContext
  if (!AudioCtx) return null
  try {
    const context = new AudioCtx()
    if (
      typeof context.createMediaStreamSource !== 'function'
      || typeof context.createBiquadFilter !== 'function'
      || typeof context.createMediaStreamDestination !== 'function'
    ) {
      void context.close?.().catch(() => undefined)
      return null
    }
    const source = context.createMediaStreamSource(rawStream as unknown as MediaStream)
    const highpass = context.createBiquadFilter()
    highpass.type = 'highpass'
    // 135 Hz strips laptop cooling fan fundamental & blade rumble without touching Vietnamese formants.
    highpass.frequency.value = 135
    highpass.Q.value = 0.707
    const destination = context.createMediaStreamDestination()
    source.connect(highpass)
    highpass.connect(destination)
    if (context.state === 'suspended') {
      void context.resume().catch(() => undefined)
    }
    return {
      stream: destination.stream as unknown as MediaStreamLike,
      close: () => {
        try { source.disconnect() } catch { /* ignore */ }
        try { highpass.disconnect() } catch { /* ignore */ }
        try { destination.stream.getTracks().forEach((track) => track.stop()) } catch { /* ignore */ }
        try { void Promise.resolve(context.close()).catch(() => undefined) } catch { /* ignore */ }
      },
    }
  } catch {
    return null
  }
}

function managedNeuralSession(vad: NeuralVadSession): NeuralVadSession {
  let startPromise: Promise<void> | null = null
  let destroyPromise: Promise<void> | null = null
  return {
    start: () => startPromise ??= Promise.resolve().then(() => vad.start()),
    // A pending start can still allocate worklets/contexts. Finish it before
    // destroying this session, and never run destroy twice after cancellation.
    destroy: () => destroyPromise ??= (startPromise ?? Promise.resolve())
      .catch(() => undefined).then(() => vad.destroy()),
  }
}

export class VoiceConversationController {
  private readonly options: VoiceConversationOptions
  private readonly dependencies: VoiceControllerDependencies
  private locale: string
  private deviceId: string
  private active = false
  private destroyed = false
  private currentMode: VoiceMode = 'idle'
  private currentPhase: VoicePhase = 'idle'
  private permission: VoicePermission = 'unknown'
  private recognition: SpeechRecognitionLike | null = null
  private recognitionStartTimer: number | null = null
  private recorderStartTimer: number | null = null
  private recognitionRestartTimer: number | null = null
  private stream: MediaStreamLike | null = null
  private audioContext: AudioContextLike | null = null
  private audioSource: AudioMediaStreamSourceLike | null = null
  private analyser: AudioAnalyserLike | null = null
  private pcmBuffer: PcmCaptureBuffer | null = null
  private pcmWorkletNode: PcmWorkletNodeLike | null = null
  private neuralVad: NeuralVadSession | null = null
  private neuralDenoiser: BrowserSpeechDenoiser | null = null
  private neuralFilterHandle: HighPassStreamHandle | null = null
  private neuralTurnHoldTimer: number | null = null
  private neuralUnavailable = false
  private monitorTimer: number | null = null
  private segmentRecorder: MediaRecorderLike | null = null
  private segmentChunks: Blob[] = []
  private segmentStartedAt = 0
  private lastVoiceAt = 0
  private lastPollAt: number | null = null
  private noiseFloor = 0.008
  private stationaryNoiseSamples = 0
  private voicedDurationMs = 0
  private readonly envelopeGate = new SpeechEnvelopeGate()
  private forceSegmentEmit = false
  private generation = 0
  private bargeInMode = false
  private bargeInReadyAt = 0
  private bargeInTriggered = false
  private bargeInDisarmedAt = 0
  private bargeInPreRollResetToken = 0
  private bargeInSustainedMs = 0
  private bargeInSpeechPeakRms = 0
  private bargeInNeuralFrames = 0
  private bargeInStrongFrames = 0
  private bargeInImmediateCandidate = false
  private bargeInAmbientRms = 0.012
  private fallbackPlaybackEnvelope = 0
  private fallbackSegmentStartedInBargeIn = false
  private fallbackSegmentConfirmedBargeIn = false
  private neuralNoiseFloor = 0.008
  private neuralNoiseSamples = 0

  constructor(options: VoiceConversationOptions) {
    this.options = options
    this.dependencies = { ...defaultDependencies(), ...options }
    this.locale = options.locale
    this.deviceId = options.deviceId ?? ''
  }

  get mode(): VoiceMode {
    return this.currentMode
  }

  get phase(): VoicePhase {
    return this.currentPhase
  }

  get isActive(): boolean {
    return this.active
  }

  async start(): Promise<void> {
    if (this.destroyed || this.active) return
    // A previous segment may still be shutting down; invalidate callbacks first.
    if (this.segmentRecorder) return
    this.active = true
    const token = ++this.generation
    if (this.options.preferNeuralVad && !this.neuralUnavailable) {
      await this.startNeuralRecorder(token)
      return
    }
    const Recognition = this.dependencies.getRecognition?.() ?? null
    if (Recognition && !this.options.preferRecorder) {
      try {
        this.startRecognition(Recognition, token)
        return
      } catch (error) {
        this.stopRecognition()
        await this.startRecorder(token, error)
        return
      }
    }
    await this.startRecorder(token)
  }

  async resume(): Promise<void> {
    if (this.destroyed) return
    if (this.active) return
    await this.start()
  }

  pause(): void {
    this.active = false
    this.bargeInMode = false
    this.bargeInReadyAt = 0
    this.bargeInTriggered = false
    this.bargeInDisarmedAt = 0
    this.bargeInSustainedMs = 0
    this.bargeInSpeechPeakRms = 0
    this.bargeInNeuralFrames = 0
    this.bargeInStrongFrames = 0
    this.bargeInImmediateCandidate = false
    this.bargeInAmbientRms = 0.012
    this.fallbackPlaybackEnvelope = 0
    this.fallbackSegmentStartedInBargeIn = false
    this.fallbackSegmentConfirmedBargeIn = false
    this.generation += 1
    this.clearRecognitionStart()
    this.clearRecorderStart()
    this.clearRecognitionRestart()
    this.stopRecognition()
    this.stopMonitor(true)
    this.options.onLevel?.(0)
    this.setState('idle', 'idle')
  }

  stop(): void {
    this.destroyed = true
    this.pause()
  }

  setBargeInMode(enabled: boolean, graceMs = BARGE_IN_GRACE_MS): void {
    const now = this.dependencies.now?.() ?? Date.now()
    const wasTriggered = this.bargeInTriggered
    this.bargeInMode = enabled
    this.bargeInReadyAt = enabled ? now + Math.max(0, graceMs) : 0
    this.bargeInTriggered = false
    this.bargeInSustainedMs = 0
    this.bargeInSpeechPeakRms = 0
    this.bargeInNeuralFrames = 0
    this.bargeInStrongFrames = 0
    this.bargeInImmediateCandidate = false
    if (enabled) {
      this.bargeInAmbientRms = Math.min(0.08, Math.max(0.012, this.neuralNoiseFloor))
      this.fallbackPlaybackEnvelope = 0
    } else if (!wasTriggered) {
      // Normal end-of-speech disarm (not a confirmed human barge-in): flush
      // buffered speaker pre-roll and record when disarm completed so lingering
      // VAD segments started during Mai's speech cannot leak out to STT.
      this.bargeInDisarmedAt = now
      this.bargeInPreRollResetToken += 1
      this.fallbackPlaybackEnvelope = 0
      if (this.segmentRecorder && this.fallbackSegmentStartedInBargeIn && !this.fallbackSegmentConfirmedBargeIn) {
        this.stopSegment(true)
      }
    }
  }

  private confirmBargeIn(mode: VoiceMode): void {
    if (!this.bargeInMode || this.bargeInTriggered) return
    this.bargeInTriggered = true
    this.bargeInMode = false
    this.fallbackSegmentConfirmedBargeIn = true
    this.options.onBargeIn?.()
    this.setState(mode, 'recording')
  }

  setLocale(locale: string): void {
    this.locale = locale
  }

  setInputDevice(deviceId: string): void {
    if (this.deviceId !== deviceId) {
      this.deviceId = deviceId
      // Reset room noise floor calibration only when switching physical microphones.
      this.neuralNoiseFloor = 0.008
      this.neuralNoiseSamples = 0
    }
  }

  stopCapture(): void {
    if (this.segmentRecorder?.state === 'recording') {
      this.active = false
      this.clearRecognitionRestart()
      this.stopRecognition()
      this.stopMonitor(false)
      this.setState('idle', 'idle')
      this.stopSegment(true, true)
      return
    }
    this.pause()
  }

  private setState(mode: VoiceMode, phase: VoicePhase): void {
    this.currentMode = mode
    this.currentPhase = phase
    this.options.onState({ mode, phase, permission: this.permission })
  }

  private emitError(kind: VoiceErrorKind, message: string, cause?: unknown): void {
    this.active = false
    if (kind === 'permission') this.permission = 'denied'
    this.options.onError({ kind, message, cause })
    this.setState('idle', 'idle')
  }

  private startRecognition(Recognition: SpeechRecognitionConstructor, token: number): void {
    if (!this.active || token !== this.generation) return
    this.clearRecognitionRestart()
    const recognition = this.dependencies.createRecognition?.() ?? new Recognition()
    this.recognition = recognition
    recognition.lang = this.locale
    recognition.continuous = true
    recognition.interimResults = true
    recognition.maxAlternatives = 1
    recognition.onstart = () => {
      if (!this.active || token !== this.generation) return
      this.clearRecognitionStart()
      this.permission = 'granted'
      this.setState('recognition', 'listening')
    }
    recognition.onresult = (event) => {
      if (!this.active || token !== this.generation) return
      const now = this.dependencies.now?.() ?? Date.now()
      const startIndex = event.resultIndex ?? 0
      let hasAnyTranscript = false
      for (let index = startIndex; index < event.results.length; index += 1) {
        if (event.results[index]?.[0]?.transcript?.trim()) {
          hasAnyTranscript = true
          break
        }
      }
      if (this.bargeInMode) {
        if (!hasAnyTranscript || now < this.bargeInReadyAt) return
        this.confirmBargeIn('recognition')
      }
      const text = finalTranscript(event)
      if (!text) return
      this.pause()
      this.options.onTranscript(text)
    }
    recognition.onerror = (event) => {
      if (!this.active || token !== this.generation) return
      if (
        event.error === 'not-allowed'
        || event.error === 'service-not-allowed'
        || event.error === 'network'
      ) {
        this.clearRecognitionStart()
        void this.fallbackToRecorder(token)
        return
      }
      this.options.onError({
        kind: 'runtime',
        message: event.message || event.error || 'Speech recognition failed',
      })
    }
    recognition.onend = () => {
      if (!this.active || token !== this.generation) return
      this.clearRecognitionStart()
      this.recognition = null
      this.recognitionRestartTimer = this.schedule(() => {
        this.recognitionRestartTimer = null
        if (!this.active || token !== this.generation) return
        try {
          this.startRecognition(Recognition, token)
        } catch (error) {
          void this.startRecorder(token, error)
        }
      }, 350)
    }
    this.recognitionStartTimer = this.schedule(() => {
      this.recognitionStartTimer = null
      if (!this.active || token !== this.generation || this.recognition !== recognition) return
      void this.fallbackToRecorder(token)
    }, RECOGNITION_START_TIMEOUT_MS)
    recognition.start()
  }

  private async fallbackToRecorder(token: number): Promise<void> {
    if (!this.active || token !== this.generation) return
    this.stopRecognition()
    try {
      await this.startRecorder(token)
    } catch (error) {
      this.emitError('permission', 'Microphone permission is required for voice conversation.', error)
    }
  }

  private async startNeuralRecorder(token: number): Promise<void> {
    if (!this.active || token !== this.generation) return
    // Neural inference requires a secure-context AudioWorklet. Do not attempt
    // to initialize ONNX in unsupported browsers, jsdom or HTTP deployments.
    if (!this.dependencies.createNeuralVad && typeof AudioWorkletNode === 'undefined') {
      await this.startRecorder(token)
      return
    }
    const getUserMedia = this.dependencies.getUserMedia
    if (!getUserMedia) {
      await this.startRecorder(token)
      return
    }
    let stream: MediaStreamLike | null = null
    let vad: NeuralVadSession | null = null
    let speechStartedAt: number | null = null
    let neuralRealStarted = false
    const preRollFrames: Float32Array[] = []
    let observedPreRollResetToken = this.bargeInPreRollResetToken
    let streamingSpeechActive = false
    let segmentPeakRms = 0
    let segmentFrameCount = 0
    let segmentHighSpeechFrames = 0
    let consecutiveVoicedFrames = 0
    let maxConsecutiveVoicedFrames = 0
    let segmentVoicedRmsSum = 0
    let lastStrongVoiceAt = 0
    let segmentStartedInBargeIn = false
    let segmentConfirmedBargeIn = false
    let bargeInPlaybackEnvelope = 0
    let postBargeInTotalFrames = 0
    let postBargeInVoicedAfterTailFrames = 0
    let postConfirmAudioFrames: Float32Array[] = []
    let postDisarmVoicedFrames = 0
    let postDisarmAudioFrames: Float32Array[] = []
    let denoisedSegmentFrames: Float32Array[] = []
    let postDisarmPromoted = false
    let pendingContinuationChunks: Float32Array[] = []
    let pendingHoldStartedAt = 0
    let continuationHoldsUsed = 0
    let evaluatingTurnHold = false
    const neuralEnvelope = new SpeechEnvelopeGate()
    const clearTurnHoldTimer = () => {
      if (this.neuralTurnHoldTimer !== null) {
        this.clearTimer(this.neuralTurnHoldTimer)
        this.neuralTurnHoldTimer = null
      }
    }
    const emitFinalNeuralAudio = (finalAudio: Float32Array) => {
      if (!this.active || token !== this.generation) return
      clearTurnHoldTimer()
      pendingContinuationChunks = []
      pendingHoldStartedAt = 0
      continuationHoldsUsed = 0
      evaluatingTurnHold = false
      streamingSpeechActive = false
      preRollFrames.length = 0
      if (!finalAudio.length || finalAudio.length < 16_000 * 0.2) {
        this.options.onSpeechMisfire?.()
        this.setState('recorder', 'listening')
        return
      }
      if (finalAudio.length > 16_000 * MAX_SEGMENT_DURATION_MS / 1000) {
        this.active = false
        this.options.onSpeechMisfire?.()
        this.releaseAudioResources()
        this.emitError('runtime', 'Câu nói dài hơn 12 giây. Bạn chia yêu cầu thành câu ngắn hơn nhé.')
        return
      }
      const capture = new PcmCaptureBuffer(16_000, 0)
      capture.start()
      capture.append(finalAudio)
      const wav = capture.finish()
      const metadata = captureMetadata(stream, 'silero-v6')
      this.active = false
      this.releaseAudioResources()
      this.lastPollAt = null
      this.options.onCapture?.(metadata)
      this.options.onAudio(wav)
    }
    const requiredBargeInRms = () => {
      // Scale strictly from ACTUAL acoustic bleed measured on the microphone
      // (bargeInAmbientRms), NOT raw digital MP3 RMS — because Chrome AEC can
      // suppress a 0.30 digital TTS track down to 0.015 on the mic, whereas
      // blasting speakers without AEC raise bargeInAmbientRms to 0.08-0.12.
      // When STT echo-verification gates barge-in, allow a slightly gentler
      // threshold so shoppers don't need to shout over Mai while echo text is
      // still rejected 100% by isLikelyAssistantEcho().
      const sttGuarded = Boolean(this.options.requireSttForBargeIn)
      const ambientMultiplier = sttGuarded
        ? (bargeInPlaybackEnvelope >= BARGE_IN_SPEAKER_ACTIVE_ENVELOPE ? 1.35 : 1.25)
        : (bargeInPlaybackEnvelope >= BARGE_IN_SPEAKER_ACTIVE_ENVELOPE ? 1.55 : 1.4)
      const ambientFloor = Math.min(0.165, this.bargeInAmbientRms * ambientMultiplier)
      const baseMin = sttGuarded ? 0.036 : BARGE_IN_NEURAL_MIN_RMS
      const activeMargin = bargeInPlaybackEnvelope >= BARGE_IN_SPEAKER_ACTIVE_ENVELOPE
        ? baseMin + 0.008
        : baseMin
      return Math.max(activeMargin, ambientFloor)
    }
    const canConfirmNeuralBargeIn = (now: number, allowImmediateOnly = false) => {
      const requiredFrames = bargeInPlaybackEnvelope >= BARGE_IN_SPEAKER_ACTIVE_ENVELOPE
        ? BARGE_IN_NEURAL_ACTIVE_SPEAKER_FRAMES
        : BARGE_IN_NEURAL_SUSTAINED_FRAMES
      const allowImmediate = this.bargeInImmediateCandidate
        && bargeInPlaybackEnvelope < BARGE_IN_SPEAKER_ACTIVE_ENVELOPE
        && this.bargeInAmbientRms < 0.045
      if (allowImmediateOnly) {
        return now >= this.bargeInReadyAt && this.bargeInNeuralFrames > 0 && allowImmediate
      }
      return (
        now >= this.bargeInReadyAt
        && this.bargeInNeuralFrames > 0
        && (this.bargeInStrongFrames >= requiredFrames || allowImmediate)
      )
    }
    const triggerNeuralBargeIn = () => {
      segmentConfirmedBargeIn = true
      postBargeInTotalFrames = 0
      postBargeInVoicedAfterTailFrames = 0
      const keepCount = Math.min(preRollFrames.length, Math.max(4, Math.min(7, this.bargeInStrongFrames + 1)))
      const bargeInPreRoll = preRollFrames.slice(-keepCount)
      preRollFrames.length = 0
      postConfirmAudioFrames = bargeInPreRoll.map((buffered) => new Float32Array(buffered))
      this.confirmBargeIn('recorder')
      if (!streamingSpeechActive) {
        streamingSpeechActive = true
        this.options.onSpeechStartFrame?.(bargeInPreRoll)
      }
    }
    const resetSegmentMetrics = () => {
      segmentPeakRms = 0
      segmentFrameCount = 0
      segmentHighSpeechFrames = 0
      consecutiveVoicedFrames = 0
      maxConsecutiveVoicedFrames = 0
      segmentVoicedRmsSum = 0
      lastStrongVoiceAt = 0
      segmentStartedInBargeIn = false
      segmentConfirmedBargeIn = false
      postBargeInTotalFrames = 0
      postBargeInVoicedAfterTailFrames = 0
      postConfirmAudioFrames = []
      postDisarmVoicedFrames = 0
      postDisarmAudioFrames = []
      denoisedSegmentFrames = []
      postDisarmPromoted = false
      neuralEnvelope.reset()
      this.bargeInSpeechPeakRms = 0
      this.bargeInNeuralFrames = 0
      this.bargeInStrongFrames = 0
      this.bargeInImmediateCandidate = false
    }
    try {
      this.recorderStartTimer = this.schedule(() => {
        if (!this.active || token !== this.generation) return
        this.recorderStartTimer = null
        this.pause()
        this.emitError('runtime', 'Không tải được bộ nhận diện giọng nói. Hãy thử lại micro.')
      }, 12_000)
      const constraints = typeof MICROPHONE_CONSTRAINTS.audio === 'object'
        ? MICROPHONE_CONSTRAINTS.audio : {}
      stream = await getUserMedia(this.deviceId
        ? { audio: { ...constraints, deviceId: { exact: this.deviceId } } }
        : MICROPHONE_CONSTRAINTS)
      if (!this.active || token !== this.generation) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      this.stream = stream
      // Route real browser streams through a 135 Hz high-pass filter before Silero VAD
      // to suppress AC hum, laptop fan rumble, and desk bumps without altering tests.
      const filteredHandle = this.dependencies.createNeuralVad ? null : buildPreVadHighPassStream(stream)
      this.neuralFilterHandle = filteredHandle
      const vadInputStream = filteredHandle?.stream ?? stream
      // Warm up real-time GTCRN WASM neural speech denoiser (196 KB) in parallel.
      const denoiserFactory = this.dependencies.createDenoiser
        ?? (this.dependencies.createNeuralVad ? undefined : createBrowserGtcrnDenoiser)
      if (denoiserFactory) {
        void Promise.resolve(denoiserFactory())
          .then((instance) => {
            if (!instance) return
            if (!this.active || token !== this.generation) {
              instance.destroy()
              return
            }
            this.neuralDenoiser?.destroy()
            this.neuralDenoiser = instance
          })
          .catch(() => undefined)
      }
      const callbacks: NeuralVadCallbacks = {
        getStream: async () => vadInputStream as unknown as MediaStream,
        onSpeechStart: () => {
          if (this.active && token === this.generation) {
            // If the shopper resumes speaking during a Smart Turn continuation hold,
            // cancel the pending flush timer and seamlessly continue the same turn!
            if (pendingContinuationChunks.length > 0) {
              clearTurnHoldTimer()
              evaluatingTurnHold = false
              const gapMs = Math.min(1_200, Math.max(0, (this.dependencies.now?.() ?? Date.now()) - pendingHoldStartedAt))
              const gapSamples = Math.round((gapMs / 1000) * 16_000)
              if (gapSamples > 0) {
                pendingContinuationChunks.push(new Float32Array(gapSamples))
              }
              pendingHoldStartedAt = 0
            }
            speechStartedAt = this.dependencies.now?.() ?? Date.now()
            neuralRealStarted = false
            resetSegmentMetrics()
            lastStrongVoiceAt = speechStartedAt
            segmentStartedInBargeIn = this.bargeInMode
          }
        },
        // An onset may still be fan airflow or a short noise/misfire. Only
        // confirmed speech above the room/fan floor triggers recording/WS STT
        // or interrupts kiosk TTS.
        onSpeechRealStart: () => {
          if (!this.active || token !== this.generation) return
          neuralRealStarted = true
          if (!this.bargeInMode) {
            if (segmentStartedInBargeIn && !segmentConfirmedBargeIn && !postDisarmPromoted) {
              return
            }
            const minVoiceRms = Math.max(
              NEURAL_MIN_SPEECH_RMS,
              this.neuralNoiseFloor * NEURAL_NOISE_FLOOR_MULTIPLIER,
            )
            if (
              segmentFrameCount > 0
              && (
                segmentPeakRms < minVoiceRms
                || segmentHighSpeechFrames < NEURAL_ONSET_MIN_VOICED_FRAMES
                || maxConsecutiveVoicedFrames < NEURAL_ONSET_MIN_CONSECUTIVE_FRAMES
              )
            ) {
              return
            }
            this.setState('recorder', 'recording')
            if (!streamingSpeechActive) {
              streamingSpeechActive = true
              this.options.onSpeechStartFrame?.(preRollFrames.splice(0, preRollFrames.length))
            }
            return
          }
          if (this.options.requireSttForBargeIn) {
            return
          }
          const now = this.dependencies.now?.() ?? Date.now()
          if (canConfirmNeuralBargeIn(now)) {
            triggerNeuralBargeIn()
          }
        },
        onVADMisfire: () => {
          if (this.active && token === this.generation) {
            speechStartedAt = null
            neuralRealStarted = false
            resetSegmentMetrics()
            if (pendingContinuationChunks.length > 0) {
              // The brief resumed sound was only a click/breath; emit the held first clause.
              emitFinalNeuralAudio(mergeFloat32Chunks(pendingContinuationChunks))
              return
            }
            if (streamingSpeechActive) {
              streamingSpeechActive = false
              this.options.onSpeechMisfire?.()
            }
            this.setState('recorder', 'listening')
          }
        },
        onFrameProcessed: (probabilities, frame) => {
          if (!this.active || token !== this.generation || !frame.length) return
          const now = this.dependencies.now?.() ?? Date.now()
          if (observedPreRollResetToken !== this.bargeInPreRollResetToken) {
            observedPreRollResetToken = this.bargeInPreRollResetToken
            preRollFrames.length = 0
            bargeInPlaybackEnvelope = 0
          }
          if (speechStartedAt !== null && now - speechStartedAt >= MAX_SEGMENT_DURATION_MS) {
            this.active = false
            clearTurnHoldTimer()
            pendingContinuationChunks = []
            if (streamingSpeechActive) {
              streamingSpeechActive = false
              this.options.onSpeechMisfire?.()
            }
            this.releaseAudioResources()
            this.emitError('runtime', 'Câu nói dài hơn 12 giây. Bạn chia yêu cầu thành câu ngắn hơn nhé.')
            return
          }
          const denoisedMetrics = this.neuralDenoiser ? this.neuralDenoiser.processFrame(frame) : null
          const activeFrame = denoisedMetrics ? denoisedMetrics.frame : frame
          let sum = 0
          for (const sample of activeFrame) sum += sample * sample
          const frameRms = denoisedMetrics ? denoisedMetrics.denoisedRms : Math.sqrt(sum / activeFrame.length)
          const frameZcr = frameZeroCrossingRate(activeFrame)
          const speechProb = typeof probabilities?.isSpeech === 'number' ? probabilities.isSpeech : 0
          const rawPlayback = this.options.getPlaybackLevel?.() ?? 0
          const livePlayback = Number.isFinite(rawPlayback) && rawPlayback > 0 ? Math.min(1, rawPlayback) : 0
          bargeInPlaybackEnvelope = Math.max(livePlayback, bargeInPlaybackEnvelope * 0.88)

          if (this.bargeInMode && !this.bargeInTriggered) {
            if (now < this.bargeInReadyAt) {
              // Auto-calibrate to actual acoustic speaker bleed on the mic during grace.
              const target = Math.min(0.14, frameRms)
              const alpha = target > this.bargeInAmbientRms ? 0.30 : 0.08
              this.bargeInAmbientRms += (target - this.bargeInAmbientRms) * alpha
            } else {
              const currentReq = requiredBargeInRms()
              if (
                frameRms < currentReq
                && (speechStartedAt === null || this.bargeInStrongFrames === 0)
              ) {
                const target = Math.min(0.13, frameRms)
                const alpha = target > this.bargeInAmbientRms ? 0.14 : 0.08
                this.bargeInAmbientRms += (target - this.bargeInAmbientRms) * alpha
              }
            }
          } else if (
            bargeInPlaybackEnvelope < 0.015
            && (
              (speechStartedAt === null && speechProb < 0.35)
              || (!streamingSpeechActive && speechProb < 0.30)
            )
          ) {
            const clamped = Math.min(0.08, Math.max(0.004, frameRms))
            if (this.neuralNoiseSamples < 6) {
              this.neuralNoiseSamples += 1
              this.neuralNoiseFloor = ((this.neuralNoiseFloor * (this.neuralNoiseSamples - 1)) + clamped) / this.neuralNoiseSamples
            } else {
              this.neuralNoiseFloor += (clamped - this.neuralNoiseFloor) * NEURAL_NOISE_FLOOR_ALPHA
            }
          }

          if (speechStartedAt !== null) {
            if (this.bargeInMode && !segmentConfirmedBargeIn) {
              segmentStartedInBargeIn = true
            }
            segmentFrameCount += 1
            if (frameRms > segmentPeakRms) segmentPeakRms = frameRms
            neuralEnvelope.observe(frameRms, 32)
            const minVoiceRms = Math.max(
              NEURAL_MIN_SPEECH_RMS,
              this.neuralNoiseFloor * NEURAL_NOISE_FLOOR_MULTIPLIER,
            )
            const isConfirmedVoicedFrame = (
              speechProb >= 0.65
              && frameRms >= minVoiceRms
              && frameZcr <= NEURAL_MAX_ZCR_FOR_VOICED
            )
            if (isConfirmedVoicedFrame) {
              segmentHighSpeechFrames += 1
              consecutiveVoicedFrames += 1
              if (consecutiveVoicedFrames > maxConsecutiveVoicedFrames) {
                maxConsecutiveVoicedFrames = consecutiveVoicedFrames
              }
              segmentVoicedRmsSum += frameRms
              lastStrongVoiceAt = now
            } else if (speechProb < 0.45 || frameRms < minVoiceRms * 0.75) {
              consecutiveVoicedFrames = 0
            }

            // Neutral-zone watchdog: if fan/AC hiss holds Silero open in the neutral zone
            // without ever producing sustained human speech, silently reset instead of
            // hanging until the 12-second error timer fires.
            if (
              !this.bargeInMode
              && !streamingSpeechActive
              && segmentHighSpeechFrames < NEURAL_ONSET_MIN_VOICED_FRAMES
              && now - Math.max(speechStartedAt, lastStrongVoiceAt) >= NEURAL_UNCONFIRMED_STALL_MS
            ) {
              speechStartedAt = null
              neuralRealStarted = false
              resetSegmentMetrics()
              preRollFrames.length = 0
              this.setState('recorder', 'listening')
              this.options.onLevel?.(Math.min(1, frameRms / 0.15))
              return
            }

            if (denoisedMetrics && !this.bargeInMode) {
              if (denoisedSegmentFrames.length === 0 && preRollFrames.length > 0) {
                for (const buffered of preRollFrames) denoisedSegmentFrames.push(new Float32Array(buffered))
              }
              denoisedSegmentFrames.push(new Float32Array(activeFrame))
            }
            if (this.bargeInMode) {
              this.bargeInNeuralFrames += 1
              if (frameRms > this.bargeInSpeechPeakRms) this.bargeInSpeechPeakRms = frameRms
              const minBargeRms = requiredBargeInRms()
              if (now >= this.bargeInReadyAt && speechProb >= BARGE_IN_NEURAL_MIN_PROB && frameRms >= minBargeRms) {
                this.bargeInStrongFrames += 1
                if (
                  bargeInPlaybackEnvelope < BARGE_IN_SPEAKER_ACTIVE_ENVELOPE
                  && this.bargeInAmbientRms < 0.045
                  && frameRms >= BARGE_IN_NEURAL_IMMEDIATE_RMS
                  && speechProb >= 0.88
                ) {
                  this.bargeInImmediateCandidate = true
                }
              } else {
                // Leaky decay preserves short Vietnamese monosyllabic interruptions
                // across a 1-frame unvoiced consonant dip while still rejecting brief speaker peaks.
                this.bargeInStrongFrames = Math.max(0, this.bargeInStrongFrames - 1)
                this.bargeInImmediateCandidate = false
              }
              const sustainedReady = now >= this.bargeInReadyAt && this.bargeInStrongFrames >= (
                bargeInPlaybackEnvelope >= BARGE_IN_SPEAKER_ACTIVE_ENVELOPE
                  ? BARGE_IN_NEURAL_ACTIVE_SPEAKER_FRAMES
                  : BARGE_IN_NEURAL_SUSTAINED_FRAMES
              )
              if (
                !this.options.requireSttForBargeIn
                && !this.bargeInTriggered
                && (sustainedReady || (neuralRealStarted && canConfirmNeuralBargeIn(now, true)))
              ) {
                triggerNeuralBargeIn()
              }
            } else if (segmentConfirmedBargeIn) {
              postBargeInTotalFrames += 1
              if (
                postBargeInTotalFrames > BARGE_IN_POST_CONFIRM_TAIL_FRAMES
                && speechProb >= 0.65
                && frameRms >= minVoiceRms
              ) {
                postBargeInVoicedAfterTailFrames += 1
              }
              postConfirmAudioFrames.push(new Float32Array(activeFrame))
            } else if (segmentStartedInBargeIn) {
              // Silero opened this segment while Mai was speaking and never confirmed
              // barge-in. Drop speaker tail frames, but still recover cleanly if a real
              // customer starts speaking after Mai has fully settled.
              if (postDisarmPromoted) {
                postDisarmAudioFrames.push(new Float32Array(activeFrame))
              } else if (
                now >= this.bargeInDisarmedAt + BARGE_IN_POST_DISARM_MARGIN_MS
                && speechProb >= 0.72
                && frameRms >= minVoiceRms
              ) {
                postDisarmVoicedFrames += 1
                postDisarmAudioFrames.push(new Float32Array(activeFrame))
                if (postDisarmVoicedFrames >= BARGE_IN_POST_DISARM_VOICED_FRAMES) {
                  postDisarmPromoted = true
                  this.setState('recorder', 'recording')
                  if (!streamingSpeechActive) {
                    streamingSpeechActive = true
                    this.options.onSpeechStartFrame?.(postDisarmAudioFrames.slice())
                  }
                }
              } else {
                postDisarmVoicedFrames = 0
                postDisarmAudioFrames = []
              }
            } else if (
              neuralRealStarted
              && !streamingSpeechActive
              && segmentPeakRms >= minVoiceRms
              && segmentHighSpeechFrames >= NEURAL_ONSET_MIN_VOICED_FRAMES
              && maxConsecutiveVoicedFrames >= NEURAL_ONSET_MIN_CONSECUTIVE_FRAMES
            ) {
              this.setState('recorder', 'recording')
              streamingSpeechActive = true
              this.options.onSpeechStartFrame?.(preRollFrames.splice(0, preRollFrames.length))
            }

            if (streamingSpeechActive) {
              this.options.onSpeechFrame?.(activeFrame)
            } else if (!segmentStartedInBargeIn || this.bargeInMode) {
              preRollFrames.push(new Float32Array(activeFrame))
              if (preRollFrames.length > 14) preRollFrames.shift()
            }
          } else {
            if (streamingSpeechActive && pendingContinuationChunks.length > 0) {
              // Keep feeding silence/continuation frames into the open WS STT during hold.
              this.options.onSpeechFrame?.(activeFrame)
            } else {
              preRollFrames.push(new Float32Array(activeFrame))
              if (preRollFrames.length > 12) preRollFrames.shift()
            }
          }
          this.options.onLevel?.(Math.min(1, frameRms / 0.15))
        },
        onSpeechEnd: (audio) => {
          if (!this.active || token !== this.generation) return
          const observedFrames = segmentFrameCount
          const observedPeakRms = segmentPeakRms
          const observedHighFrames = segmentHighSpeechFrames
          const observedMaxConsecutiveVoiced = maxConsecutiveVoicedFrames
          const observedVoicedRmsSum = segmentVoicedRmsSum
          const wasStartedInBargeIn = segmentStartedInBargeIn
          const wasConfirmedBargeIn = segmentConfirmedBargeIn
          const wasPostDisarmPromoted = postDisarmPromoted
          const observedPostBargeInFrames = postBargeInTotalFrames
          const observedPostBargeInVoiced = postBargeInVoicedAfterTailFrames
          const trimmedPostConfirmFrames = postConfirmAudioFrames
          const trimmedPostDisarmFrames = postDisarmAudioFrames
          const capturedDenoisedFrames = denoisedSegmentFrames
          const isStationaryFanNoise = observedFrames >= 12 && neuralEnvelope.stationaryNoiseLevel() !== null
          speechStartedAt = null
          neuralRealStarted = false
          preRollFrames.length = 0
          resetSegmentMetrics()
          if ((this.bargeInMode || wasStartedInBargeIn) && !wasConfirmedBargeIn && !wasPostDisarmPromoted) {
            streamingSpeechActive = false
            if (
              this.options.requireSttForBargeIn
              && this.options.onBargeInCandidateAudio
              && !isStationaryFanNoise
              && observedPeakRms >= requiredBargeInRms()
              && observedHighFrames >= 5
              && observedMaxConsecutiveVoiced >= 3
              && audio.length >= 16_000 * 0.25
              && audio.length <= 16_000 * 8
            ) {
              const candidateCapture = new PcmCaptureBuffer(16_000, 0)
              candidateCapture.start()
              candidateCapture.append(audio)
              this.options.onBargeInCandidateAudio(candidateCapture.finish())
              return
            }
            this.options.onSpeechMisfire?.()
            this.setState('recorder', 'listening')
            return
          }
          // Post-barge-in continuation gate: if a loud speaker spike cut off Mai
          // and no human continued speaking after the ~160ms speaker decay tail,
          // silently discard it instead of sending Mai's own voice to STT.
          if (
            wasConfirmedBargeIn
            && observedPostBargeInFrames >= BARGE_IN_POST_CONFIRM_MIN_OBSERVED
            && observedPostBargeInVoiced < BARGE_IN_POST_CONFIRM_MIN_VOICED
          ) {
            streamingSpeechActive = false
            this.options.onSpeechMisfire?.()
            this.setState('recorder', 'listening')
            return
          }
          const minVoiceRms = Math.max(
            NEURAL_MIN_SPEECH_RMS,
            this.neuralNoiseFloor * NEURAL_NOISE_FLOOR_MULTIPLIER,
          )
          const meanVoicedRms = observedHighFrames > 0 ? observedVoicedRmsSum / observedHighFrames : 0
          const isSparseClicks = observedFrames >= 6 && (
            observedHighFrames < NEURAL_END_MIN_VOICED_FRAMES
            || observedMaxConsecutiveVoiced < NEURAL_ONSET_MIN_CONSECUTIVE_FRAMES
            || (observedHighFrames < 8 && observedHighFrames / observedFrames < 0.12)
            || (observedHighFrames < 6 && observedPeakRms > 0 && meanVoicedRms < observedPeakRms * 0.22)
          )
          if (
            observedFrames > 0
            && !wasPostDisarmPromoted
            && (
              observedPeakRms < minVoiceRms
              || isStationaryFanNoise
              || isSparseClicks
            )
          ) {
            if (pendingContinuationChunks.length > 0) {
              // Prior held speech was real; this trailing bump was only ambient noise.
              emitFinalNeuralAudio(mergeFloat32Chunks(pendingContinuationChunks))
              return
            }
            streamingSpeechActive = false
            this.options.onSpeechMisfire?.()
            this.setState('recorder', 'listening')
            return
          }
          let segmentAudio = audio
          if (wasPostDisarmPromoted && trimmedPostDisarmFrames.length > 0) {
            segmentAudio = mergeFloat32Chunks(trimmedPostDisarmFrames)
          } else if (wasConfirmedBargeIn && trimmedPostConfirmFrames.length >= 10) {
            const mergedConfirm = mergeFloat32Chunks(trimmedPostConfirmFrames)
            if (mergedConfirm.length >= 16_000 * 0.2) {
              segmentAudio = mergedConfirm
            }
          } else if (capturedDenoisedFrames.length >= 6) {
            const mergedDenoised = mergeFloat32Chunks(capturedDenoisedFrames)
            if (mergedDenoised.length >= 16_000 * 0.2) {
              segmentAudio = mergedDenoised
            }
          }
          const combinedAudio = pendingContinuationChunks.length > 0
            ? mergeFloat32Chunks([...pendingContinuationChunks, segmentAudio])
            : segmentAudio

          // Optional Smart Turn v3 / Vietnamese incomplete-clause continuation hold:
          // when the shopper pauses mid-thought ("Cho mình hỏi là..."), keep Silero
          // and WebSocket STT alive briefly so they can finish without being cut off.
          if (
            this.options.shouldHoldTurnEnd
            && !this.bargeInMode
            && !evaluatingTurnHold
            && continuationHoldsUsed < MAX_TURN_CONTINUATION_HOLDS
            && combinedAudio.length >= 16_000 * 0.2
            && combinedAudio.length < 16_000 * (MAX_SEGMENT_DURATION_MS / 1000 - 1.5)
          ) {
            const decision = this.options.shouldHoldTurnEnd()
            const applyHoldDecision = (rawHoldMs: number) => {
              if (!this.active || token !== this.generation || speechStartedAt !== null) return
              const holdMs = Number.isFinite(rawHoldMs) && rawHoldMs > 0
                ? Math.min(1_400, Math.round(rawHoldMs))
                : 0
              if (holdMs > 0) {
                pendingContinuationChunks = [combinedAudio]
                pendingHoldStartedAt = this.dependencies.now?.() ?? Date.now()
                continuationHoldsUsed += 1
                clearTurnHoldTimer()
                this.neuralTurnHoldTimer = this.schedule(() => {
                  this.neuralTurnHoldTimer = null
                  if (!this.active || token !== this.generation || speechStartedAt !== null) return
                  const ready = pendingContinuationChunks.length > 0
                    ? mergeFloat32Chunks(pendingContinuationChunks)
                    : combinedAudio
                  emitFinalNeuralAudio(ready)
                }, holdMs)
                return
              }
              emitFinalNeuralAudio(combinedAudio)
            }
            if (typeof decision === 'number') {
              applyHoldDecision(decision)
              return
            }
            evaluatingTurnHold = true
            pendingContinuationChunks = [combinedAudio]
            pendingHoldStartedAt = this.dependencies.now?.() ?? Date.now()
            void Promise.resolve(decision)
              .then((ms) => {
                evaluatingTurnHold = false
                applyHoldDecision(ms)
              })
              .catch(() => {
                evaluatingTurnHold = false
                applyHoldDecision(0)
              })
            return
          }

          emitFinalNeuralAudio(combinedAudio)
        },
      }
      const factory = this.dependencies.createNeuralVad ?? createLocalNeuralVad
      vad = managedNeuralSession(await factory(callbacks))
      if (!this.active || token !== this.generation) {
        this.destroyNeuralSession(vad)
        this.stopOwnedStream(stream)
        return
      }
      this.neuralVad = vad
      await vad.start()
      if (!this.active || token !== this.generation) {
        if (this.neuralVad === vad) this.neuralVad = null
        this.destroyNeuralSession(vad)
        this.stopOwnedStream(stream)
        return
      }
      this.clearRecorderStart()
      this.permission = 'granted'
      this.options.onInputDevice?.(stream.getTracks()[0]?.label || 'Micro mặc định')
      this.setState('recorder', 'listening')
    } catch (error) {
      if (!this.active || token !== this.generation) {
        if (vad && this.neuralVad !== vad) this.destroyNeuralSession(vad)
        this.stopOwnedStream(stream)
        return
      }
      this.clearRecorderStart()
      this.releaseAudioResources()
      const name = (error as { name?: string })?.name
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        this.emitError('permission', 'Microphone permission is required for voice conversation.', error)
        return
      }
      this.neuralUnavailable = true
      // A model download, WASM or microphone capability error is not
      // allowed to make an otherwise working kiosk lose its voice input.
      await this.startRecorder(token, error)
    }
  }

  private async startRecorder(token: number, cause?: unknown): Promise<void> {
    if (!this.active || token !== this.generation) return
    this.clearRecognitionStart()
    const getUserMedia = this.dependencies.getUserMedia
    const Recorder = this.dependencies.MediaRecorder
    const createAudioContext = this.dependencies.createAudioContext
    if (!getUserMedia || !createAudioContext || (!Recorder && !this.options.preferPcmWorklet)) {
      this.emitError(
        'unsupported',
        'This browser cannot start microphone recording. Please allow microphone access or use text input.',
        cause,
      )
      return
    }

    let stream: MediaStreamLike | null = null
    let context: AudioContextLike | null = null
    let source: AudioMediaStreamSourceLike | null = null
    let node: PcmWorkletNodeLike | null = null
    try {
      this.recorderStartTimer = this.schedule(() => {
        if (!this.active || token !== this.generation) return
        this.recorderStartTimer = null
        this.pause()
        this.emitError('runtime', 'Chưa mở được micro. Hãy kiểm tra quyền micro rồi bấm mic để thử lại.')
      }, 12_000)
      const audioConstraints = typeof MICROPHONE_CONSTRAINTS.audio === 'object' ? MICROPHONE_CONSTRAINTS.audio : {}
      stream = await getUserMedia(this.deviceId
        ? { audio: { ...audioConstraints, deviceId: { exact: this.deviceId } } }
        : MICROPHONE_CONSTRAINTS)
      if (!this.active || token !== this.generation) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      this.stream = stream
      context = createAudioContext()
      this.audioContext = context
      if (context.resume && context.state !== 'running') await context.resume()
      if (!this.active || token !== this.generation) {
        if (this.audioContext === context) this.releaseAudioResources()
        return
      }
      if (context.state === 'suspended') {
        throw new Error('Bộ xử lý âm thanh chưa chạy. Bạn bấm mic để thử lại nhé.')
      }
      const analyser = context.createAnalyser()
      analyser.fftSize = 512
      source = context.createMediaStreamSource(stream)
      source.connect(analyser)
      this.stream = stream
      this.audioContext = context
      this.audioSource = source
      this.analyser = analyser
      if (this.options.preferPcmWorklet && context.audioWorklet
        && context.sampleRate && context.destination !== undefined) {
        // Worklet is progressive enhancement. Older browsers and restricted
        // contexts continue using MediaRecorder rather than losing voice mode.
        node = null
        try {
          await context.audioWorklet.addModule(`${import.meta.env.BASE_URL}pcm-capture.worklet.js`)
          if (!this.active || token !== this.generation) {
            this.releaseLocalRecorderResources(stream, context, source, node)
            return
          }
          node = this.dependencies.createPcmWorkletNode
            ? this.dependencies.createPcmWorkletNode(context)
            : new AudioWorkletNode(context as unknown as AudioContext, 'retail-pcm-capture') as unknown as PcmWorkletNodeLike
          const buffer = new PcmCaptureBuffer(context.sampleRate, 350)
          node.port.onmessage = (event) => {
            if (this.active && token === this.generation && event.data instanceof Float32Array) {
              buffer.append(event.data)
            }
          }
          source.connect(node)
          node.connect(context.destination)
          this.pcmBuffer = buffer
          this.pcmWorkletNode = node
        } catch {
          if (!this.active || token !== this.generation) {
            this.releaseLocalRecorderResources(stream, context, source, node)
            return
          }
          if (node) {
            node.port.onmessage = null
            node.disconnect()
          }
          if (!Recorder) throw new Error('AudioWorklet recording unavailable')
        }
      }
      if (!Recorder && !this.pcmBuffer) throw new Error('No supported microphone recorder')
      this.clearRecorderStart()
      this.permission = 'granted'
      this.options.onInputDevice?.(stream.getTracks()[0]?.label || 'Micro mặc định')
      this.noiseFloor = 0.008
      this.stationaryNoiseSamples = 0
      this.lastPollAt = this.dependencies.now?.() ?? Date.now()
      this.setState('recorder', 'listening')
      this.pollAudio(token)
    } catch (error) {
      if (token !== this.generation || !this.active) {
        this.releaseLocalRecorderResources(stream, context, source, node)
        return
      }
      this.clearRecorderStart()
      this.stopMonitor(true)
      const name = (error as { name?: string })?.name
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        this.emitError('permission', 'Microphone permission is required for voice conversation.', error)
      } else {
        this.emitError('runtime', name === 'NotFoundError'
          ? 'Không tìm thấy micro. Bạn kiểm tra thiết bị đầu vào nhé.'
          : name === 'NotReadableError'
            ? 'Không mở được micro. Bạn kiểm tra xem ứng dụng khác có đang giữ micro không.'
            : error instanceof Error ? error.message : 'Không khởi động được micro.', error)
      }
    }
  }

  private pollAudio(token: number): void {
    if (!this.active || token !== this.generation || this.currentMode !== 'recorder' || !this.analyser) return
    const data = new Uint8Array(this.analyser.fftSize)
    this.analyser.getByteTimeDomainData(data)
    const level = rms(data)
    this.options.onLevel?.(Math.min(1, level / 0.15))
    const now = this.dependencies.now?.() ?? Date.now()
    const elapsed = this.lastPollAt === null
      ? POLL_INTERVAL_MS
      : Math.max(0, Math.min(now - this.lastPollAt, POLL_INTERVAL_MS * 2))
    this.lastPollAt = now
    let threshold = Math.max(
      MIN_SPEECH_THRESHOLD,
      Math.min(MAX_SPEECH_THRESHOLD, this.noiseFloor * 2),
    )
    if (!this.segmentRecorder && level < threshold) {
      this.noiseFloor += (level - this.noiseFloor) * NOISE_FLOOR_ALPHA
      threshold = Math.max(
        MIN_SPEECH_THRESHOLD,
        Math.min(MAX_SPEECH_THRESHOLD, this.noiseFloor * 2),
      )
    }
    // Do not lower the threshold below the quiet-room floor once recording:
    // otherwise a fan/AGC background can hold the recorder open until 12s.
    if (this.bargeInMode) {
      const rawPlayback = this.options.getPlaybackLevel?.() ?? 0
      const livePlayback = Number.isFinite(rawPlayback) && rawPlayback > 0 ? Math.min(1, rawPlayback) : 0
      this.fallbackPlaybackEnvelope = Math.max(livePlayback, this.fallbackPlaybackEnvelope * 0.85)
      if (now < this.bargeInReadyAt) {
        const target = Math.min(0.14, level)
        const alpha = target > this.bargeInAmbientRms ? 0.30 : 0.08
        this.bargeInAmbientRms += (target - this.bargeInAmbientRms) * alpha
        this.monitorTimer = this.schedule(() => {
          this.monitorTimer = null
          this.pollAudio(token)
        }, POLL_INTERVAL_MS)
        return
      }
      const bargeInThreshold = Math.max(
        BARGE_IN_FALLBACK_MIN_RMS,
        threshold * 1.5,
        Math.min(0.165, this.bargeInAmbientRms * 1.55),
      )
      const bargeInDetected = level >= (this.segmentRecorder
        ? Math.max(MIN_SPEECH_THRESHOLD, bargeInThreshold * 0.85)
        : bargeInThreshold)
      if (bargeInDetected) {
        if (!this.segmentRecorder) this.startSegment(now, true)
        this.envelopeGate.observe(level, elapsed)
        this.lastVoiceAt = now
        this.voicedDurationMs += elapsed
        this.bargeInSustainedMs += elapsed
        if (
          !this.options.requireSttForBargeIn
          && !this.bargeInTriggered
          && this.bargeInSustainedMs >= BARGE_IN_FALLBACK_CONFIRM_MS
        ) {
          this.confirmBargeIn('recorder')
        }
      } else if (this.segmentRecorder && !this.bargeInTriggered && now - this.lastVoiceAt >= 240) {
        this.bargeInSustainedMs = 0
        this.stopSegment(true)
        return
      } else if (!this.segmentRecorder) {
        this.bargeInSustainedMs = 0
        const target = Math.min(0.24, level)
        this.bargeInAmbientRms += (target - this.bargeInAmbientRms) * 0.1
      }
      if (this.segmentRecorder && now - this.segmentStartedAt >= MAX_SEGMENT_DURATION_MS) {
        this.stopSegment(false)
        this.releaseAudioResources()
        this.emitError('runtime', 'Câu nói dài hơn 12 giây. Bạn chia yêu cầu thành câu ngắn hơn nhé.')
        return
      }
      this.monitorTimer = this.schedule(() => {
        this.monitorTimer = null
        this.pollAudio(token)
      }, POLL_INTERVAL_MS)
      return
    }
    const speechDetected = level >= (this.segmentRecorder
      ? Math.max(MIN_SPEECH_THRESHOLD, threshold * 0.88)
      : threshold)
    if (speechDetected) {
      // Recorder starts on the FIRST possible speech frame. We decide whether
      // to upload only after accumulating evidence, preserving initial syllables.
      if (!this.segmentRecorder) this.startSegment(now)
      this.envelopeGate.observe(level, elapsed)
      this.lastVoiceAt = now
      this.voicedDurationMs += elapsed
      // Flat, sustained noise should not keep the recorder in RECORDING for 12s.
      // Adapt the floor only after stationary evidence; real fluctuating speech
      // is intentionally left to normal endpointing and the upload gate.
      const stationary = this.options.validateSpeechEnvelope
        ? this.envelopeGate.stationaryNoiseLevel()
        : null
      if (stationary !== null) {
        this.stationaryNoiseSamples += 1
        if (this.stationaryNoiseSamples >= 3) {
          this.noiseFloor = Math.max(this.noiseFloor, Math.min(MAX_SPEECH_THRESHOLD / 1.6, stationary * 0.7))
          this.stopSegment(true)
          return
        }
      } else {
        this.stationaryNoiseSamples = 0
      }
    }
    if (this.segmentRecorder && now - this.segmentStartedAt >= MAX_SEGMENT_DURATION_MS) {
      this.stopSegment(false)
      this.releaseAudioResources()
      this.emitError('runtime', 'Câu nói dài hơn 12 giây. Bạn chia yêu cầu thành câu ngắn hơn nhé.')
      return
    }
    if (this.segmentRecorder && now - this.lastVoiceAt >= SILENCE_DURATION_MS) {
      this.stopSegment(true)
      return
    }
    this.monitorTimer = this.schedule(() => {
      this.monitorTimer = null
      this.pollAudio(token)
    }, POLL_INTERVAL_MS)
  }

  private startSegment(now: number, deferRecordingState = false): void {
    const Recorder = this.dependencies.MediaRecorder
    if (!this.stream || (!Recorder && !this.pcmBuffer)) return
    const selectedMimeType = Recorder
      ? ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
        .find((candidate) => Recorder.isTypeSupported?.(candidate))
      : undefined
    try {
      const recorder: MediaRecorderLike = this.pcmBuffer
        ? new PcmRecorderAdapter(this.pcmBuffer)
        : new Recorder!(this.stream, selectedMimeType ? { mimeType: selectedMimeType } : undefined)
      const mimeType = this.pcmBuffer ? 'audio/wav' : selectedMimeType ?? recorder.mimeType ?? 'audio/webm'
      const token = this.generation
      this.segmentChunks = []
      this.segmentStartedAt = now
      this.lastVoiceAt = now
      this.voicedDurationMs = 0
      this.envelopeGate.reset()
      this.stationaryNoiseSamples = 0
      this.forceSegmentEmit = false
      this.fallbackSegmentStartedInBargeIn = this.bargeInMode
      this.fallbackSegmentConfirmedBargeIn = false
      this.segmentRecorder = recorder
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) this.segmentChunks.push(event.data)
      }
      recorder.onstop = () => {
        const blob = new Blob(this.segmentChunks, { type: mimeType })
        const unconfirmedBargeIn = (this.bargeInMode || this.fallbackSegmentStartedInBargeIn)
          && !this.fallbackSegmentConfirmedBargeIn
        const envelopeValid = this.voicedDurationMs >= MIN_VOICED_DURATION_MS
          && (!this.options.validateSpeechEnvelope || this.envelopeGate.hasSpeechEvidence())
        const hasSpeech = !unconfirmedBargeIn && (this.forceSegmentEmit || envelopeValid)
        const emitSttBargeInCandidate = unconfirmedBargeIn
          && Boolean(this.options.requireSttForBargeIn)
          && Boolean(this.options.onBargeInCandidateAudio)
          && blob.size > 0
          && envelopeValid
        this.segmentRecorder = null
        this.segmentChunks = []
        this.voicedDurationMs = 0
        this.bargeInSustainedMs = 0
        this.fallbackSegmentStartedInBargeIn = false
        this.fallbackSegmentConfirmedBargeIn = false
        this.envelopeGate.reset()
        this.stationaryNoiseSamples = 0
        this.forceSegmentEmit = false
        if (emitSttBargeInCandidate && token === this.generation && !this.destroyed) {
          this.options.onBargeInCandidateAudio?.(blob)
          this.active = true
          this.lastPollAt = this.dependencies.now?.() ?? Date.now()
          this.setState('recorder', 'listening')
          this.pollAudio(token)
        } else if (blob.size > 0 && token === this.generation && hasSpeech) {
          const metadata = captureMetadata(this.stream, this.pcmBuffer ? 'pcm' : 'mediarecorder')
          this.releaseAudioResources()
          this.lastPollAt = null
          this.options.onCapture?.(metadata)
          this.options.onAudio(blob)
        } else if (token === this.generation && !this.destroyed) {
          this.active = true
          this.lastPollAt = this.dependencies.now?.() ?? Date.now()
          this.setState('recorder', 'listening')
          this.pollAudio(token)
        } else {
          this.releaseAudioResources()
          this.lastPollAt = null
        }
      }
      recorder.onerror = (event) => {
        // Invalidate callbacks and release the microphone, even if the browser
        // never follows onerror with onstop.
        this.stopMonitor(true)
        this.emitError('runtime', event.error || 'Audio recording failed')
      }
      recorder.start(100)
      if (!deferRecordingState) this.setState('recorder', 'recording')
    } catch (error) {
      this.segmentRecorder = null
      this.segmentChunks = []
      this.releaseAudioResources()
      this.emitError('runtime', 'Audio recording could not start.', error)
    }
  }

  private stopSegment(emitAudio: boolean, forceEmit = false): void {
    const recorder = this.segmentRecorder
    if (!recorder) return
    this.active = false
    this.stopMonitor(false)
    if (!emitAudio) {
      this.forceSegmentEmit = false
      recorder.ondataavailable = null
      recorder.onstop = null
      this.segmentRecorder = null
      this.segmentChunks = []
    }
    try {
      if (forceEmit) this.forceSegmentEmit = true
      if (recorder.state !== 'inactive') recorder.stop()
    } catch (error) {
      this.segmentRecorder = null
      this.segmentChunks = []
      this.forceSegmentEmit = false
      this.releaseAudioResources()
      if (emitAudio) this.emitError('runtime', 'Audio recording could not stop.', error)
    }
  }

  private stopRecognition(): void {
    this.clearRecognitionStart()
    const recognition = this.recognition
    this.recognition = null
    if (!recognition) return
    recognition.onstart = null
    recognition.onresult = null
    recognition.onerror = null
    recognition.onend = null
    try {
      recognition.stop()
    } catch {
      recognition.abort?.()
    }
  }

  private clearRecorderStart(): void {
    if (this.recorderStartTimer !== null) this.clearTimer(this.recorderStartTimer)
    this.recorderStartTimer = null
  }

  private destroyNeuralSession(vad: NeuralVadSession | null): void {
    if (vad) void Promise.resolve(vad.destroy()).catch(() => undefined)
  }

  private stopOwnedStream(stream: MediaStreamLike | null): void {
    // A late initialization may belong to an earlier generation. Never stop a
    // stream that a newer generation has already adopted.
    if (!stream || this.stream === stream) return
    stream.getTracks().forEach((track) => track.stop())
  }

  private releaseLocalRecorderResources(
    stream: MediaStreamLike | null,
    context: AudioContextLike | null,
    source: AudioMediaStreamSourceLike | null,
    node: PcmWorkletNodeLike | null,
  ): void {
    if (node && node !== this.pcmWorkletNode) {
      node.port.onmessage = null
      node.disconnect()
    }
    if (source !== this.audioSource) source?.disconnect?.()
    if (context && context !== this.audioContext) {
      try { void Promise.resolve(context.close()).catch(() => undefined) } catch { /* Already closed. */ }
    }
    this.stopOwnedStream(stream)
  }

  private stopMonitor(discardSegment: boolean): void {
    if (this.monitorTimer !== null) {
      this.clearTimer(this.monitorTimer)
      this.monitorTimer = null
    }
    if (discardSegment && this.segmentRecorder) this.stopSegment(false)
    if (discardSegment) this.releaseAudioResources()
  }

  private releaseAudioResources(): void {
    if (this.neuralTurnHoldTimer !== null) {
      this.clearTimer(this.neuralTurnHoldTimer)
      this.neuralTurnHoldTimer = null
    }
    const vad = this.neuralVad
    this.neuralVad = null
    this.destroyNeuralSession(vad)
    const denoiser = this.neuralDenoiser
    this.neuralDenoiser = null
    denoiser?.destroy()
    const filterHandle = this.neuralFilterHandle
    this.neuralFilterHandle = null
    filterHandle?.close()
    if (this.pcmWorkletNode) {
      this.pcmWorkletNode.port.onmessage = null
      this.pcmWorkletNode.disconnect()
      this.pcmWorkletNode = null
    }
    this.pcmBuffer?.clear()
    this.pcmBuffer = null
    this.audioSource?.disconnect?.()
    this.audioSource = null
    this.analyser = null
    const context = this.audioContext
    this.audioContext = null
    if (context) {
      try { void Promise.resolve(context.close()).catch(() => undefined) } catch { /* Already closed. */ }
    }
    const stream = this.stream
    this.stream = null
    stream?.getTracks().forEach((track) => track.stop())
  }

  private clearRecognitionRestart(): void {
    if (this.recognitionRestartTimer !== null) {
      this.clearTimer(this.recognitionRestartTimer)
      this.recognitionRestartTimer = null
    }
  }

  private clearRecognitionStart(): void {
    if (this.recognitionStartTimer !== null) {
      this.clearTimer(this.recognitionStartTimer)
      this.recognitionStartTimer = null
    }
  }

  private schedule(handler: () => void, timeout: number): number {
    return (this.dependencies.setTimeout ?? ((callback, delay) => setTimeout(callback, delay) as unknown as number))(handler, timeout)
  }

  private clearTimer(timer: number): void {
    (this.dependencies.clearTimeout ?? ((value) => clearTimeout(value)))(timer)
  }
}
