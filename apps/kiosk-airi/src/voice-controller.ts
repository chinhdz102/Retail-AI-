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
}

export interface MediaStreamLike {
  getTracks: () => MediaStreamTrackLike[]
}

export interface VoiceMediaConstraints {
  audio: boolean | {
    echoCancellation?: boolean
    noiseSuppression?: boolean
    autoGainControl?: boolean
  }
}

export interface MediaRecorderDataEventLike {
  data: Blob
}

export interface MediaRecorderLike {
  state: 'inactive' | 'recording' | 'paused'
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
  connect: (node: AudioAnalyserLike) => void
  disconnect?: () => void
}

export interface AudioContextLike {
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
  setTimeout?: (handler: () => void, timeout: number) => number
  clearTimeout?: (timer: number) => void
  now?: () => number
}

export interface VoiceConversationOptions extends VoiceControllerDependencies {
  locale: string
  onState: (state: VoiceState) => void
  onTranscript: (text: string) => void
  onAudio: (audio: Blob) => void
  onError: (error: VoiceControllerError) => void
}

const SPEECH_THRESHOLD = 0.035
const SILENCE_DURATION_MS = 600
const MAX_SEGMENT_DURATION_MS = 12_000
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
  private active = false
  private destroyed = false
  private currentMode: VoiceMode = 'idle'
  private currentPhase: VoicePhase = 'idle'
  private permission: VoicePermission = 'unknown'
  private recognition: SpeechRecognitionLike | null = null
  private recognitionStartTimer: number | null = null
  private recognitionRestartTimer: number | null = null
  private stream: MediaStreamLike | null = null
  private audioContext: AudioContextLike | null = null
  private audioSource: AudioMediaStreamSourceLike | null = null
  private analyser: AudioAnalyserLike | null = null
  private monitorTimer: number | null = null
  private segmentRecorder: MediaRecorderLike | null = null
  private segmentChunks: Blob[] = []
  private segmentStartedAt = 0
  private lastVoiceAt = 0
  private generation = 0

  constructor(options: VoiceConversationOptions) {
    this.options = options
    this.dependencies = { ...defaultDependencies(), ...options }
    this.locale = options.locale
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
    this.active = true
    const token = ++this.generation
    const Recognition = this.dependencies.getRecognition?.() ?? null
    if (Recognition) {
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
    this.active = false
    await this.start()
  }

  pause(): void {
    this.active = false
    this.generation += 1
    this.clearRecognitionStart()
    this.clearRecognitionRestart()
    this.stopRecognition()
    this.stopMonitor(true)
    this.setState('idle', 'idle')
  }

  stop(): void {
    this.destroyed = true
    this.pause()
  }

  setLocale(locale: string): void {
    this.locale = locale
  }

  stopCapture(): void {
    if (this.segmentRecorder?.state === 'recording') {
      this.active = false
      this.clearRecognitionRestart()
      this.stopRecognition()
      this.stopMonitor(false)
      this.setState('idle', 'idle')
      this.stopSegment(true)
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
    if (!getUserMedia || !Recorder || !createAudioContext) {
      this.emitError(
        'unsupported',
        'This browser cannot start microphone recording. Please allow microphone access or use text input.',
        cause,
      )
      return
    }

    try {
      const stream = await getUserMedia(MICROPHONE_CONSTRAINTS)
      if (!this.active || token !== this.generation) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      const context = createAudioContext()
      const analyser = context.createAnalyser()
      analyser.fftSize = 512
      const source = context.createMediaStreamSource(stream)
      source.connect(analyser)
      this.stream = stream
      this.audioContext = context
      this.audioSource = source
      this.analyser = analyser
      this.permission = 'granted'
      this.setState('recorder', 'listening')
      this.pollAudio(token)
    } catch (error) {
      this.stopMonitor(true)
      this.emitError('permission', 'Microphone permission is required for voice conversation.', error)
    }
  }

  private pollAudio(token: number): void {
    if (!this.active || token !== this.generation || this.currentMode !== 'recorder' || !this.analyser) return
    const data = new Uint8Array(this.analyser.fftSize)
    this.analyser.getByteTimeDomainData(data)
    const level = rms(data)
    const now = this.dependencies.now?.() ?? Date.now()
    if (level >= SPEECH_THRESHOLD) {
      if (!this.segmentRecorder) this.startSegment(now)
      this.lastVoiceAt = now
    } else if (this.segmentRecorder) {
      const silentFor = now - this.lastVoiceAt
      const segmentAge = now - this.segmentStartedAt
      if (silentFor >= SILENCE_DURATION_MS || segmentAge >= MAX_SEGMENT_DURATION_MS) {
        this.stopSegment(true)
        return
      }
    }
    this.monitorTimer = this.schedule(() => {
      this.monitorTimer = null
      this.pollAudio(token)
    }, POLL_INTERVAL_MS)
  }

  private startSegment(now: number): void {
    const Recorder = this.dependencies.MediaRecorder
    if (!Recorder || !this.stream) return
    const mimeType = Recorder.isTypeSupported?.('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : 'audio/webm'
    try {
      const recorder = new Recorder(this.stream, { mimeType })
      const token = this.generation
      this.segmentChunks = []
      this.segmentStartedAt = now
      this.lastVoiceAt = now
      this.segmentRecorder = recorder
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) this.segmentChunks.push(event.data)
      }
      recorder.onstop = () => {
        const blob = new Blob(this.segmentChunks, { type: mimeType })
        this.segmentRecorder = null
        this.segmentChunks = []
        this.releaseAudioResources()
        if (blob.size > 0 && token === this.generation) this.options.onAudio(blob)
      }
      recorder.onerror = (event) => {
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

  private stopSegment(emitAudio: boolean): void {
    const recorder = this.segmentRecorder
    if (!recorder) return
    this.active = false
    this.stopMonitor(false)
    if (!emitAudio) {
      recorder.ondataavailable = null
      recorder.onstop = null
      this.segmentRecorder = null
      this.segmentChunks = []
    }
    try {
      if (recorder.state !== 'inactive') recorder.stop()
    } catch (error) {
      this.segmentRecorder = null
      this.segmentChunks = []
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

  private stopMonitor(discardSegment: boolean): void {
    if (this.monitorTimer !== null) {
      this.clearTimer(this.monitorTimer)
      this.monitorTimer = null
    }
    if (discardSegment && this.segmentRecorder) this.stopSegment(false)
    if (discardSegment) this.releaseAudioResources()
  }

  private releaseAudioResources(): void {
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
