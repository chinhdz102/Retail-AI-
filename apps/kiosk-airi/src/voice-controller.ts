import { SpeechEnvelopeGate } from './voice-speech-gate'
import { PcmCaptureBuffer } from './voice-pcm'

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
}

export interface MediaStreamLike {
  getTracks: () => MediaStreamTrackLike[]
}

export interface VoiceMediaConstraints {
  audio: boolean | {
    echoCancellation?: boolean
    noiseSuppression?: boolean
    autoGainControl?: boolean
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
  onState: (state: VoiceState) => void
  onTranscript: (text: string) => void
  onAudio: (audio: Blob) => void
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

  constructor(private readonly buffer: PcmCaptureBuffer) {}

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
const MICROPHONE_CONSTRAINTS: VoiceMediaConstraints = {
  audio: {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
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

  setLocale(locale: string): void {
    this.locale = locale
  }

  setInputDevice(deviceId: string): void {
    this.deviceId = deviceId
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

  private async startRecorder(token: number, cause?: unknown): Promise<void> {
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

    try {
      this.recorderStartTimer = this.schedule(() => {
        this.recorderStartTimer = null
        if (!this.active || token !== this.generation) return
        this.pause()
        this.emitError('runtime', 'Chưa mở được micro. Hãy kiểm tra quyền micro rồi bấm mic để thử lại.')
      }, 12_000)
      const audioConstraints = typeof MICROPHONE_CONSTRAINTS.audio === 'object' ? MICROPHONE_CONSTRAINTS.audio : {}
      const stream = await getUserMedia(this.deviceId
        ? { audio: { ...audioConstraints, deviceId: { exact: this.deviceId } } }
        : MICROPHONE_CONSTRAINTS)
      if (!this.active || token !== this.generation) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      const context = createAudioContext()
      this.stream = stream
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
      const source = context.createMediaStreamSource(stream)
      source.connect(analyser)
      this.stream = stream
      this.audioContext = context
      this.audioSource = source
      this.analyser = analyser
      if (this.options.preferPcmWorklet && context.audioWorklet
        && context.sampleRate && context.destination !== undefined) {
        // Worklet is progressive enhancement. Older browsers and restricted
        // contexts continue using MediaRecorder rather than losing voice mode.
        let node: PcmWorkletNodeLike | null = null
        try {
          await context.audioWorklet.addModule(`${import.meta.env.BASE_URL}pcm-capture.worklet.js`)
          if (!this.active || token !== this.generation) {
            this.releaseAudioResources()
            return
          }
          node = this.dependencies.createPcmWorkletNode
            ? this.dependencies.createPcmWorkletNode(context)
            : new AudioWorkletNode(context as AudioContext, 'retail-pcm-capture') as unknown as PcmWorkletNodeLike
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
      this.clearRecorderStart()
      if (token !== this.generation) return
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
    if (this.segmentRecorder && (
      now - this.lastVoiceAt >= SILENCE_DURATION_MS
      || now - this.segmentStartedAt >= MAX_SEGMENT_DURATION_MS
    )) {
      this.stopSegment(true)
      return
    }
    this.monitorTimer = this.schedule(() => {
      this.monitorTimer = null
      this.pollAudio(token)
    }, POLL_INTERVAL_MS)
  }

  private startSegment(now: number): void {
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
      this.segmentRecorder = recorder
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) this.segmentChunks.push(event.data)
      }
      recorder.onstop = () => {
        const blob = new Blob(this.segmentChunks, { type: mimeType })
        const hasSpeech = this.forceSegmentEmit || (
          this.voicedDurationMs >= MIN_VOICED_DURATION_MS
          && (!this.options.validateSpeechEnvelope || this.envelopeGate.hasSpeechEvidence())
        )
        this.segmentRecorder = null
        this.segmentChunks = []
        this.voicedDurationMs = 0
        this.envelopeGate.reset()
        this.stationaryNoiseSamples = 0
        this.forceSegmentEmit = false
        if (blob.size > 0 && token === this.generation && hasSpeech) {
          this.releaseAudioResources()
          this.lastPollAt = null
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
      this.setState('recorder', 'recording')
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

  private stopMonitor(discardSegment: boolean): void {
    if (this.monitorTimer !== null) {
      this.clearTimer(this.monitorTimer)
      this.monitorTimer = null
    }
    if (discardSegment && this.segmentRecorder) this.stopSegment(false)
    if (discardSegment) this.releaseAudioResources()
  }

  private releaseAudioResources(): void {
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
    if (context) void context.close()
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
