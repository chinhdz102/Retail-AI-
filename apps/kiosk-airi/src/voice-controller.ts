import type { RealTimeVADOptions } from '@ricky0123/vad-web'

export type VoiceMode = 'idle' | 'recorder'
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

export interface MediaStreamTrackLike {
  stop: () => void
  label?: string
}

export interface MediaStreamLike {
  getTracks: () => MediaStreamTrackLike[]
}

export interface VoiceMediaConstraints {
  audio: boolean | {
    channelCount?: number
    echoCancellation?: boolean
    noiseSuppression?: boolean
    autoGainControl?: boolean
    deviceId?: { exact: string }
  }
}

export interface VoiceVADLike {
  start: () => Promise<void> | void
  pause: () => Promise<void> | void
  destroy?: () => Promise<void> | void
}

export interface VoiceVADOptions {
  getStream: () => Promise<MediaStream>
  pauseStream: (stream: MediaStream) => Promise<void>
  resumeStream: (stream: MediaStream) => Promise<MediaStream>
  onFrameProcessed: (probabilities: { isSpeech: number; notSpeech: number }, frame: Float32Array) => void
  onSpeechStart: () => void
  onSpeechRealStart: () => void
  onSpeechEnd: (audio: Float32Array) => void
  onVADMisfire: () => void
  positiveSpeechThreshold: number
  negativeSpeechThreshold: number
  redemptionMs: number
  preSpeechPadMs: number
  minSpeechMs: number
  submitUserSpeechOnPause: boolean
  model: 'v5'
  processorType: 'auto'
  baseAssetPath: string
  onnxWASMBasePath: string
  ortConfig: NonNullable<RealTimeVADOptions['ortConfig']>
}

export interface VoiceControllerDependencies {
  getUserMedia?: (constraints: VoiceMediaConstraints) => Promise<MediaStreamLike>
  createVAD?: (options: VoiceVADOptions) => Promise<VoiceVADLike>
  setTimeout?: (handler: () => void, timeout: number) => number
  clearTimeout?: (timer: number) => void
}

export interface VoiceConversationOptions extends VoiceControllerDependencies {
  locale: string
  deviceId?: string
  preferRecorder?: boolean
  onState: (state: VoiceState) => void
  onTranscript: (text: string) => void
  onAudio: (audio: Blob) => void
  onLevel?: (level: number) => void
  onInputDevice?: (label: string) => void
  onError: (error: VoiceControllerError) => void
}

const START_TIMEOUT_MS = 12_000
const MAX_SEGMENT_DURATION_MS = 30_000
// Keep pauses inside a natural Vietnamese sentence and preserve initial consonants.
const MIN_SPEECH_MS = 400
const REDEMPTION_MS = 1_400
const PRE_SPEECH_PAD_MS = 800
const MIN_AUDIO_FRAME_RMS = 0.00075
const MIN_AUDIO_ACTIVE_MS = 160

const MICROPHONE_CONSTRAINTS: VoiceMediaConstraints = {
  audio: {
    channelCount: 1,
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  },
}

function getBrowserMediaDevices() {
  return (globalThis as typeof globalThis & {
    navigator?: { mediaDevices?: { getUserMedia?: (constraints: MediaStreamConstraints) => Promise<MediaStream> } }
  }).navigator?.mediaDevices
}

function defaultGetUserMedia(constraints: VoiceMediaConstraints): Promise<MediaStreamLike> {
  const mediaDevices = getBrowserMediaDevices()
  if (!mediaDevices?.getUserMedia) {
    return Promise.reject(new Error('Trình duyệt này không hỗ trợ microphone.'))
  }
  return mediaDevices.getUserMedia(constraints as MediaStreamConstraints)
}

async function defaultCreateVAD(options: VoiceVADOptions): Promise<VoiceVADLike> {
  const { MicVAD } = await import('@ricky0123/vad-web')
  return MicVAD.new(options as Partial<RealTimeVADOptions>)
}

function rms(samples: Float32Array): number {
  if (samples.length === 0) return 0
  let sum = 0
  for (const sample of samples) sum += sample * sample
  return Math.sqrt(sum / samples.length)
}

function hasMeaningfulAudioLevel(samples: Float32Array): boolean {
  const frameSize = 512 // 32 ms at the VAD's 16 kHz sample rate.
  let activeMilliseconds = 0
  for (let start = 0; start < samples.length; start += frameSize) {
    const frame = samples.subarray(start, Math.min(start + frameSize, samples.length))
    if (rms(frame) >= MIN_AUDIO_FRAME_RMS) {
      activeMilliseconds += (frame.length / 16_000) * 1_000
    }
  }
  return activeMilliseconds >= MIN_AUDIO_ACTIVE_MS
}

function encodePcm16Wav(samples: Float32Array): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buffer)
  const writeAscii = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index))
    }
  }

  writeAscii(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  writeAscii(8, 'WAVE')
  writeAscii(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, 16_000, true)
  view.setUint32(28, 32_000, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeAscii(36, 'data')
  view.setUint32(40, samples.length * 2, true)
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index] ?? 0))
    view.setInt16(44 + index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true)
  }

  return new Blob([buffer], { type: 'audio/wav' })
}

function vadAssetRoot(): string {
  const baseUrl = import.meta.env.BASE_URL.endsWith('/')
    ? import.meta.env.BASE_URL
    : `${import.meta.env.BASE_URL}/`
  return new URL(`${baseUrl}vad/`, window.location.origin).toString()
}

export class VoiceConversationController {
  private readonly options: VoiceConversationOptions
  private readonly dependencies: VoiceControllerDependencies
  private deviceId: string
  private active = false
  private destroyed = false
  private currentMode: VoiceMode = 'idle'
  private currentPhase: VoicePhase = 'idle'
  private permission: VoicePermission = 'unknown'
  private vad: VoiceVADLike | null = null
  private vadGeneration: number | null = null
  private vadLifecycle: Promise<void> = Promise.resolve()
  private stream: MediaStreamLike | null = null
  private startTimer: number | null = null
  private maxSegmentTimer: number | null = null
  private manualStopRequested = false
  private generation = 0

  constructor(options: VoiceConversationOptions) {
    this.options = options
    this.dependencies = options
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
    this.active = true
    const token = ++this.generation
    const setTimeoutFn = this.dependencies.setTimeout ?? ((handler, timeout) => setTimeout(handler, timeout) as unknown as number)
    this.startTimer = setTimeoutFn(() => {
      this.startTimer = null
      if (!this.active || token !== this.generation) return
      this.fail('runtime', 'Chưa mở được bộ lọc giọng nói. Hãy tải lại trang rồi thử lại nhé.')
      void this.pauseVAD()
    }, START_TIMEOUT_MS)

    try {
      await this.enqueueVADLifecycle(async () => {
        if (!this.active || token !== this.generation) return

        if (!this.vad || this.vadGeneration !== token) {
          const previousVAD = this.vad
          this.vad = null
          this.vadGeneration = null
          if (previousVAD) await this.disposeVAD(previousVAD)

          const nextVAD = await this.createVAD(token)
          if (!this.active || token !== this.generation) {
            await this.disposeVAD(nextVAD)
            return
          }
          this.vad = nextVAD
          this.vadGeneration = token
        }

        const vad = this.vad
        if (!vad) return
        await vad.start()
        if (!this.active || token !== this.generation) {
          if (this.vad === vad) {
            this.vad = null
            this.vadGeneration = null
          }
          await this.disposeVAD(vad)
          return
        }
        this.clearStartTimer()
        this.permission = 'granted'
        this.setState('recorder', 'listening')
      })
    } catch (error) {
      if (token !== this.generation) return
      this.clearStartTimer()
      const name = (error as { name?: string })?.name
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        this.fail('permission', 'Microphone permission is required for voice conversation.', error)
      } else if (name === 'NotFoundError') {
        this.fail('runtime', 'Không tìm thấy micro. Bạn kiểm tra thiết bị đầu vào nhé.', error)
      } else if (name === 'NotReadableError') {
        this.fail('runtime', 'Không mở được micro. Bạn kiểm tra xem ứng dụng khác có đang giữ micro không.', error)
      } else {
        this.fail('unsupported', 'Không tải được bộ nhận diện giọng nói. Bạn có thể nhập câu hỏi bằng chữ.', error)
      }
      await this.pauseVAD()
    }
  }

  async resume(): Promise<void> {
    if (this.destroyed) return
    this.active = false
    await this.start()
  }

  pause(): void {
    this.active = false
    this.manualStopRequested = false
    this.generation += 1
    this.clearStartTimer()
    this.clearMaxSegmentTimer()
    this.options.onLevel?.(0)
    this.setState('idle', 'idle')
    void this.pauseVAD()
  }

  stop(): void {
    this.destroyed = true
    this.active = false
    this.manualStopRequested = false
    this.generation += 1
    this.clearStartTimer()
    this.clearMaxSegmentTimer()
    this.options.onLevel?.(0)
    this.setState('idle', 'idle')
    void this.pauseVAD()
  }

  setLocale(_locale: string): void {
    // Silero detects speech independently of the language. Groq receives the selected locale.
  }

  setInputDevice(deviceId: string): void {
    this.deviceId = deviceId
  }

  stopCapture(): void {
    if (!this.vad || !this.active) {
      this.pause()
      return
    }
    this.active = false
    this.manualStopRequested = true
    this.clearStartTimer()
    this.clearMaxSegmentTimer()
    this.options.onLevel?.(0)
    this.setState('idle', 'idle')
    const vad = this.vad
    const token = this.generation
    void this.enqueueVADLifecycle(async () => {
      if (this.vad !== vad) return
      try {
        await vad.pause()
      } catch {
        // The VAD may already have stopped after the maximum segment timer.
      } finally {
        this.releaseStream()
        // onSpeechEnd clears this flag when it flushes the segment. If there was
        // no segment, clear it only after pause has completed.
        if (token === this.generation && this.manualStopRequested) {
          this.manualStopRequested = false
        }
      }
    })
  }

  private async createVAD(token: number): Promise<VoiceVADLike> {
    const createVAD = this.dependencies.createVAD ?? defaultCreateVAD
    const openMicrophone = async (): Promise<MediaStream> => {
      const constraints = this.deviceId
        ? {
            audio: {
              ...(MICROPHONE_CONSTRAINTS.audio as Exclude<VoiceMediaConstraints['audio'], boolean>),
              deviceId: { exact: this.deviceId },
            },
          }
        : MICROPHONE_CONSTRAINTS
      const getUserMedia = this.dependencies.getUserMedia ?? defaultGetUserMedia
      let stream: MediaStreamLike
      try {
        stream = await getUserMedia(constraints)
      } catch (error) {
        const name = (error as { name?: string })?.name
        const unavailableDevice = [
          'NotFoundError',
          'OverconstrainedError',
          'ConstraintNotSatisfiedError',
        ].includes(name ?? '')
        if (!this.deviceId || !unavailableDevice) throw error

        // A device ID can become stale after a USB/headset change. Keep voice usable
        // by trying the operating-system default, then refresh the picker after capture.
        this.deviceId = ''
        stream = await getUserMedia(MICROPHONE_CONSTRAINTS)
      }
      this.stream = stream
      if (this.active && token === this.generation) {
        this.permission = 'granted'
        this.options.onInputDevice?.(stream.getTracks()[0]?.label || 'Micro mặc định')
      }
      return stream as MediaStream
    }
    const stopMicrophone = async (stream: MediaStream): Promise<void> => {
      stream.getTracks().forEach((track) => track.stop())
      if (this.stream === stream) this.stream = null
    }
    const assetRoot = vadAssetRoot()
    const vad = await createVAD({
      getStream: openMicrophone,
      pauseStream: stopMicrophone,
      resumeStream: async () => openMicrophone(),
      onFrameProcessed: (_probabilities, frame) => {
        if (!this.active || token !== this.generation) return
        this.options.onLevel?.(Math.min(1, rms(frame) / 0.15))
      },
      onSpeechStart: () => {
        if (!this.active || token !== this.generation) return
        this.setState('recorder', 'recording')
        this.clearMaxSegmentTimer()
        const setTimeoutFn = this.dependencies.setTimeout ?? ((handler, timeout) => setTimeout(handler, timeout) as unknown as number)
        this.maxSegmentTimer = setTimeoutFn(() => this.stopCapture(), MAX_SEGMENT_DURATION_MS)
      },
      onSpeechRealStart: () => {
        if (this.active && token === this.generation) this.setState('recorder', 'recording')
      },
      onSpeechEnd: (audio) => { void this.finishSegment(audio, token) },
      onVADMisfire: () => {
        if (this.active && token === this.generation) {
          this.clearMaxSegmentTimer()
          this.setState('recorder', 'listening')
        }
      },
      positiveSpeechThreshold: 0.42,
      negativeSpeechThreshold: 0.28,
      redemptionMs: REDEMPTION_MS,
      preSpeechPadMs: PRE_SPEECH_PAD_MS,
      minSpeechMs: MIN_SPEECH_MS,
      submitUserSpeechOnPause: true,
      model: 'v5',
      processorType: 'auto',
      baseAssetPath: assetRoot,
      onnxWASMBasePath: assetRoot,
      ortConfig: (runtime) => {
        runtime.env.logLevel = 'error'
        runtime.env.wasm.numThreads = 1
        runtime.env.wasm.wasmPaths = {
          mjs: new URL('ort-wasm-simd-threaded.mjs', assetRoot).toString(),
          wasm: new URL('ort-wasm-simd-threaded.wasm', assetRoot).toString(),
        }
      },
    })
    return vad
  }

  private async finishSegment(audio: Float32Array, token: number): Promise<void> {
    if (token !== this.generation || (!this.active && !this.manualStopRequested) || this.destroyed) return
    const resumeAfterQuietSegment = this.active && !this.manualStopRequested
    this.active = false
    this.manualStopRequested = false
    this.clearStartTimer()
    this.clearMaxSegmentTimer()
    this.options.onLevel?.(0)
    await this.pauseVAD()
    if (token !== this.generation || this.destroyed || audio.length === 0) return
    this.setState('idle', 'idle')
    if (!hasMeaningfulAudioLevel(audio)) {
      if (resumeAfterQuietSegment) await this.start()
      return
    }
    this.options.onAudio(encodePcm16Wav(audio))
  }

  private enqueueVADLifecycle(operation: () => Promise<void>): Promise<void> {
    const task = this.vadLifecycle.catch(() => undefined).then(operation)
    this.vadLifecycle = task.then(() => undefined, () => undefined)
    return task
  }

  private async disposeVAD(vad: VoiceVADLike): Promise<void> {
    try {
      if (vad.destroy) await Promise.resolve(vad.destroy())
      else await Promise.resolve(vad.pause())
    } catch {
      // Keep shutdown best effort; MediaStream tracks are stopped below as a second guard.
    } finally {
      if (this.vad === vad) {
        this.vad = null
        this.vadGeneration = null
      }
      this.releaseStream()
    }
  }

  private pauseVAD(): Promise<void> {
    this.clearMaxSegmentTimer()
    return this.enqueueVADLifecycle(async () => {
      const vad = this.vad
      this.vad = null
      this.vadGeneration = null
      if (vad) await this.disposeVAD(vad)
      else this.releaseStream()
    })
  }

  private releaseStream(): void {
    const stream = this.stream
    this.stream = null
    stream?.getTracks().forEach((track) => track.stop())
  }

  private fail(kind: VoiceErrorKind, message: string, cause?: unknown): void {
    this.active = false
    if (kind === 'permission') this.permission = 'denied'
    this.options.onError({ kind, message, cause })
    this.setState('idle', 'idle')
  }

  private setState(mode: VoiceMode, phase: VoicePhase): void {
    this.currentMode = mode
    this.currentPhase = phase
    this.options.onState({ mode, phase, permission: this.permission })
  }

  private clearStartTimer(): void {
    if (this.startTimer !== null) {
      const clearTimeoutFn = this.dependencies.clearTimeout ?? ((timer) => clearTimeout(timer))
      clearTimeoutFn(this.startTimer)
    }
    this.startTimer = null
  }

  private clearMaxSegmentTimer(): void {
    if (this.maxSegmentTimer !== null) {
      const clearTimeoutFn = this.dependencies.clearTimeout ?? ((timer) => clearTimeout(timer))
      clearTimeoutFn(this.maxSegmentTimer)
    }
    this.maxSegmentTimer = null
  }
}
