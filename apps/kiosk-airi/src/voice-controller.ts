import { createLocalNeuralVad, type NeuralVadCallbacks, type NeuralVadSession } from './voice-neural'
import { encodeMonoWav } from './voice-pcm'

export interface VoiceState {
  mode: 'idle' | 'recorder'
  phase: 'idle' | 'listening' | 'recording'
  permission: 'unknown' | 'granted' | 'denied'
}
export interface VoiceControllerError {
  kind: 'permission' | 'unsupported' | 'runtime'
  message: string
  cause?: unknown
}
export interface VoiceConversationOptions {
  deviceId?: string
  onState: (state: VoiceState) => void
  onAudio: (audio: Blob, previewId?: number) => void
  onAudioPreview?: (audio: Blob, previewId: number) => void
  onAudioPreviewInvalidated?: (previewId: number) => void
  onLevel?: (level: number) => void
  onInputDevice?: (label: string) => void
  onNotice?: (message: string) => void
  /** Confirmed speech onset; runs before the final WAV is ready. */
  onSpeechStart?: () => void
  onVadFrame?: (probability: number) => void
  onError: (error: VoiceControllerError) => void
  createVad?: (callbacks: NeuralVadCallbacks) => Promise<NeuralVadSession>
  getUserMedia?: (constraints: MediaStreamConstraints) => Promise<MediaStream>
}

/** One local Silero capture per turn. No browser recognizer or RMS fallback. */
export class VoiceConversationController {
  private generation = 0
  private current: { close: () => void; promise: Promise<void> } | null = null
  private deviceId: string
  isActive = false
  private readonly options: VoiceConversationOptions

  constructor(options: VoiceConversationOptions) {
    this.options = options
    this.deviceId = options.deviceId ?? ''
  }

  setInputDevice(deviceId: string) { this.deviceId = deviceId }

  start(): Promise<void> {
    if (this.current) return this.current.promise
    const generation = ++this.generation
    const valid = () => generation === this.generation
    const streams = new Set<MediaStream>()
    let vad: NeuralVadSession | null = null
    let timer: ReturnType<typeof setTimeout> | undefined
    let setupTimer: ReturnType<typeof setTimeout> | undefined
    let recording = false
    const close = () => {
      clearTimeout(timer)
      clearTimeout(setupTimer)
      for (const stream of streams) stream.getTracks().forEach(track => track.stop())
      streams.clear()
      if (vad) {
        const retired = vad
        vad = null
        void Promise.resolve().then(() => retired.destroy()).catch(() => {})
      }
    }
    const state = (phase: 'listening' | 'recording') => {
      if (valid()) this.options.onState({ mode: 'recorder', phase, permission: 'granted' })
    }
    // Defer setup so pause() can always retire the current start.
    const promise = Promise.resolve().then(async () => {
      if (!valid()) return
      try {
        setupTimer = setTimeout(() => {
          if (!valid()) return
          this.pause()
          this.options.onError({ kind: 'runtime', message: 'Mở bộ thu âm quá lâu. Bạn thử bật mic lại nhé.' })
        }, 12_000)
        const createVad = this.options.createVad ?? createLocalNeuralVad
        const session = await createVad({
          getStream: async () => {
            const getMedia = this.options.getUserMedia
              ?? ((constraints: MediaStreamConstraints) => navigator.mediaDevices.getUserMedia(constraints))
            const stream = await getMedia({
              audio: {
                // Keep browser suppression/AEC, but don't stack browser AGC
                // with detector gain and the bounded server input gain.
                echoCancellation: true, noiseSuppression: true, autoGainControl: false,
                channelCount: 1,
                ...(this.deviceId ? { deviceId: { exact: this.deviceId } } : {}),
              },
              video: false,
            })
            if (!valid()) {
              stream.getTracks().forEach(track => track.stop())
              throw new Error('Capture cancelled')
            }
            streams.add(stream)
            this.options.onInputDevice?.(stream.getAudioTracks()[0]?.label ?? '')
            return stream
          },
          onSpeechStart: () => {},
          onError: (cause) => {
            if (!valid()) return
            this.pause()
            this.options.onError({ kind: 'runtime', message: 'Không xử lý được âm thanh. Bạn thử bật mic lại nhé.', cause })
          },
          onSpeechRealStart: () => {
            if (!valid()) return
            recording = true
            this.options.onSpeechStart?.()
            if (!valid()) return
            state('recording')
            clearTimeout(timer)
            timer = setTimeout(() => {
              if (!valid()) return
              // Bound a long turn without throwing away its whole recording.
              // Explicit Stop still cancels; only this duration boundary flushes.
              if (vad?.flush) {
                void Promise.resolve(vad.flush()).catch(cause => {
                  if (!valid()) return
                  this.pause()
                  this.options.onError({ kind: 'runtime', message: 'Không hoàn tất được câu vừa nói. Bạn thử lại nhé.', cause })
                })
              } else {
                this.pause()
                this.options.onError({ kind: 'runtime', message: 'Chưa hoàn tất được câu vừa nói. Bạn thử lại nhé.' })
              }
            }, 45_000)
          },
          onSpeechPreview: this.options.onAudioPreview ? (samples, id) => {
            if (valid()) this.options.onAudioPreview?.(encodeMonoWav(samples, 16_000), id)
          } : undefined,
          onSpeechPreviewInvalidated: id => {
            if (valid()) this.options.onAudioPreviewInvalidated?.(id)
          },
          onSpeechEnd: (samples, previewId) => {
            if (!valid() || !samples.length) return
            const audio = encodeMonoWav(samples, 16_000)
            // Stop physical tracks before any upload or TTS can begin.
            this.pause()
            this.options.onAudio(audio, previewId)
          },
          onVADMisfire: () => {
            if (!valid()) return
            recording = false
            clearTimeout(timer)
            state('listening')
            this.options.onNotice?.('Âm thanh vừa rồi quá ngắn hoặc chưa rõ. Bạn nói lại giúp mình nhé.')
          },
          onFrameProcessed: (_probabilities, frame) => {
            if (!valid()) return
            this.options.onVadFrame?.(_probabilities.isSpeech)
            const rms = Math.sqrt(frame.reduce((sum, value) => sum + value * value, 0) / Math.max(1, frame.length))
            this.options.onLevel?.(Math.min(1, rms * 8))
          },
        })
        if (!valid()) { await session.destroy(); return }
        vad = session
        await session.start()
        if (!valid()) { close(); return }
        clearTimeout(setupTimer)
        this.isActive = true
        if (!recording) state('listening')
      } catch (cause) {
        close()
        if (!valid()) return
        this.pause()
        const permission = cause instanceof Error && ['NotAllowedError', 'PermissionDeniedError'].includes(cause.name)
        this.options.onError({
          kind: permission ? 'permission' : 'runtime',
          message: permission ? 'Bạn cần cho phép sử dụng micro.' : 'Không mở được bộ nhận diện tiếng nói. Bạn thử bật mic lại nhé.',
          cause,
        })
      }
    })
    this.current = { close, promise }
    return promise
  }

  pause(): void {
    ++this.generation
    this.current?.close()
    this.current = null
    this.isActive = false
    this.options.onLevel?.(0)
    this.options.onState({ mode: 'idle', phase: 'idle', permission: 'unknown' })
  }
  stop(): void { this.pause() }
  stopCapture(): void { this.pause() }
}
