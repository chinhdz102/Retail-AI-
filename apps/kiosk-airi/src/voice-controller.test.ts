import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  VoiceConversationController,
  type AudioContextLike,
  type MediaRecorderLike,
  type MediaStreamLike,
  type SpeechRecognitionLike,
} from './voice-controller'

class FakeRecognition implements SpeechRecognitionLike {
  lang = ''
  continuous = false
  interimResults = false
  maxAlternatives = 0
  onstart: (() => void) | null = null
  onresult: ((event: unknown) => void) | null = null
  onerror: ((event: { error?: string }) => void) | null = null
  onend: (() => void) | null = null
  start = vi.fn(() => this.onstart?.())
  stop = vi.fn(() => this.onend?.())
}

class DelayedRecognition extends FakeRecognition {
  start = vi.fn()
}

class FakeRecorder implements MediaRecorderLike {
  static isTypeSupported = vi.fn((_candidate: string) => true)
  static lastTimeslice: number | undefined
  state: 'inactive' | 'recording' = 'inactive'
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  onerror: ((event: { error?: string }) => void) | null = null
  start = vi.fn((timeslice?: number) => {
    FakeRecorder.lastTimeslice = timeslice
    this.state = 'recording'
  })
  stop = vi.fn(() => {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['voice']) })
    this.onstop?.()
  })
}

function fakeStream(): MediaStreamLike {
  return { getTracks: () => [{ stop: vi.fn() }] }
}

function fakeAudioContext(analyser: { fftSize: number; getByteTimeDomainData(data: Uint8Array): void }): AudioContextLike {
  return {
    createMediaStreamSource: () => ({ connect: vi.fn() }),
    createAnalyser: () => analyser,
    close: vi.fn(async () => undefined),
  }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('VoiceConversationController', () => {
  it('emits only neural-validated WAV speech and frees the microphone', async () => {
    const trackStop = vi.fn()
    const onAudio = vi.fn()
    const onState = vi.fn()
    const destroy = vi.fn().mockResolvedValue(undefined)
    let callbacks!: import('./voice-neural').NeuralVadCallbacks
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferNeuralVad: true,
      getUserMedia: async () => ({ getTracks: () => [{ stop: trackStop, label: 'USB mic' }] }),
      createNeuralVad: async (value) => {
        callbacks = value
        return { start: vi.fn().mockResolvedValue(undefined), destroy }
      },
      onAudio, onTranscript: vi.fn(), onState, onError: vi.fn(),
    })
    await controller.start()
    expect(controller.phase).toBe('listening')
    callbacks.onVADMisfire()
    expect(onAudio).not.toHaveBeenCalled()
    callbacks.onSpeechStart()
    expect(controller.phase).toBe('recording')
    callbacks.onSpeechEnd(new Float32Array(8_000).fill(0.24))
    expect(onAudio).toHaveBeenCalledOnce()
    const audio = onAudio.mock.calls[0][0] as Blob
    expect(audio.type).toBe('audio/wav')
    expect(audio.size).toBe(44 + 8_000 * 2)
    expect(destroy).toHaveBeenCalledOnce()
    expect(trackStop).toHaveBeenCalledOnce()
    callbacks.onSpeechEnd(new Float32Array(8_000).fill(0.7))
    expect(onAudio).toHaveBeenCalledOnce()
    controller.stop()
  })

  it('falls back to the recorder when neural VAD model fails to initialize', async () => {
    const onError = vi.fn()
    const getUserMedia = vi.fn(async () => fakeStream())
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferNeuralVad: true,
      getUserMedia, MediaRecorder: FakeRecorder,
      createNeuralVad: async () => { throw new Error('WASM not supported') },
      createAudioContext: () => fakeAudioContext({
        fftSize: 32, getByteTimeDomainData: (data) => data.fill(128),
      }),
      onAudio: vi.fn(), onTranscript: vi.fn(), onState: vi.fn(), onError,
    })
    await controller.start()
    expect(controller.phase).toBe('listening')
    expect(getUserMedia).toHaveBeenCalledTimes(2)
    expect(onError).not.toHaveBeenCalled()
    controller.stop()
  })

  it('drops delayed neural callback when the user cancels microphone capture', async () => {
    const onAudio = vi.fn()
    const destroy = vi.fn().mockResolvedValue(undefined)
    let callbacks!: import('./voice-neural').NeuralVadCallbacks
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferNeuralVad: true,
      getUserMedia: async () => fakeStream(),
      createNeuralVad: async (value) => {
        callbacks = value
        return { start: vi.fn().mockResolvedValue(undefined), destroy }
      },
      onAudio, onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(),
    })
    await controller.start()
    controller.pause()
    callbacks.onSpeechEnd(new Float32Array(8_000).fill(0.4))
    expect(onAudio).not.toHaveBeenCalled()
    expect(destroy).toHaveBeenCalled()
    controller.stop()
  })

  it('prefers WAV AudioWorklet with pre-roll and no speaker feedback', async () => {
    vi.useFakeTimers()
    const onAudio = vi.fn()
    const getUserMedia = vi.fn(async () => fakeStream())
    const source = { connect: vi.fn(), disconnect: vi.fn() }
    let frameIndex = 0
    const analyser = {
      fftSize: 32,
      getByteTimeDomainData(data: Uint8Array) {
        const speaking = frameIndex >= 3 && frameIndex < 10
        data.fill(speaking ? [155, 200, 180, 210][frameIndex % 4] : 128)
        frameIndex += 1
      },
    }
    const addModule = vi.fn().mockResolvedValue(undefined)
    const context = {
      sampleRate: 16000,
      destination: {},
      audioWorklet: { addModule },
      createMediaStreamSource: () => source,
      createAnalyser: () => analyser,
      close: vi.fn(async () => undefined),
    }
    const node = {
      port: { onmessage: null as null | ((event: { data: Float32Array }) => void) },
      connect: vi.fn(),
      disconnect: vi.fn(),
    }
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferPcmWorklet: true,
      getUserMedia, MediaRecorder: FakeRecorder,
      createAudioContext: () => context,
      createPcmWorkletNode: () => node,
      onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(), onAudio,
      now: () => Date.now(),
    })
    await controller.start()
    expect(addModule).toHaveBeenCalledWith(expect.stringContaining('pcm-capture.worklet.js'))
    expect(node.connect).toHaveBeenCalledWith(context.destination)
    // A quieter initial consonant is buffered before the RMS start threshold.
    node.port.onmessage?.({ data: new Float32Array(160).fill(0.1) })
    await vi.advanceTimersByTimeAsync(240)
    node.port.onmessage?.({ data: new Float32Array(160).fill(0.5) })
    await vi.advanceTimersByTimeAsync(950)
    expect(onAudio).toHaveBeenCalledOnce()
    const blob = onAudio.mock.calls[0][0] as Blob
    expect(blob.type).toBe('audio/wav')
    const view = new DataView(await blob.arrayBuffer())
    expect(String.fromCharCode(...new Uint8Array(view.buffer, 0, 4))).toBe('RIFF')
    expect(view.getInt16(44, true)).toBeCloseTo(Math.floor(0.1 * 32767), -1)
    expect(node.disconnect).toHaveBeenCalled()
    controller.stop()
  })

  it('falls back to MediaRecorder when the AudioWorklet cannot load', async () => {
    const onAudio = vi.fn()
    const analyser = { fftSize: 32, getByteTimeDomainData: (data: Uint8Array) => data.fill(128) }
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferPcmWorklet: true,
      getUserMedia: async () => fakeStream(), MediaRecorder: FakeRecorder,
      createAudioContext: () => ({
        ...fakeAudioContext(analyser),
        sampleRate: 16000, destination: {},
        audioWorklet: { addModule: vi.fn().mockRejectedValue(new Error('CSP blocked')) },
      }),
      onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(), onAudio,
    })
    await controller.start()
    expect(controller.phase).toBe('listening')
    controller.stop()
  })

  it('uses MP4 when the browser does not support WebM recording', async () => {
    let selectedMimeType: string | undefined
    class Mp4Recorder extends FakeRecorder {
      static isTypeSupported = vi.fn((candidate: string) => candidate === 'audio/mp4')
      constructor(_stream: MediaStreamLike, options?: { mimeType?: string }) {
        super()
        selectedMimeType = options?.mimeType
      }
    }
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true,
      getUserMedia: async () => fakeStream(), MediaRecorder: Mp4Recorder,
      createAudioContext: () => fakeAudioContext({
        fftSize: 32, getByteTimeDomainData: (data) => data.fill(220),
      }),
      onAudio: vi.fn(), onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(),
    })
    await controller.start()
    expect(selectedMimeType).toBe('audio/mp4')
    controller.stop()
  })

  it('discards a current recording when explicitly paused', async () => {
    const onAudio = vi.fn()
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true,
      getUserMedia: async () => fakeStream(), MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext({
        fftSize: 32, getByteTimeDomainData: (data) => data.fill(220),
      }),
      onAudio, onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(),
    })
    await controller.start()
    expect(controller.phase).toBe('recording')
    controller.pause()
    expect(onAudio).not.toHaveBeenCalled()
    expect(controller.isActive).toBe(false)
    controller.stop()
  })

  it('records from the selected microphone and reports its actual label', async () => {
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop: vi.fn(), label: 'Headset microphone' }] }))
    const onInputDevice = vi.fn()
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, deviceId: 'headset-input',
      getUserMedia, MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext({ fftSize: 32, getByteTimeDomainData: (data) => data.fill(128) }),
      onAudio: vi.fn(), onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(), onInputDevice,
    })
    await controller.start()
    expect(getUserMedia).toHaveBeenCalledWith({ audio: {
      echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      deviceId: { exact: 'headset-input' },
    } })
    expect(onInputDevice).toHaveBeenCalledWith('Headset microphone')
    controller.stop()
  })

  it('reports a pending microphone permission request instead of listening forever', async () => {
    vi.useFakeTimers()
    let grantMicrophone!: (stream: MediaStreamLike) => void
    const trackStop = vi.fn()
    const onError = vi.fn()
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true,
      getUserMedia: () => new Promise((resolve) => { grantMicrophone = resolve }),
      MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext({ fftSize: 32, getByteTimeDomainData: (data) => data.fill(128) }),
      onAudio: vi.fn(), onTranscript: vi.fn(), onState: vi.fn(), onError,
    })
    const pending = controller.start()
    await vi.advanceTimersByTimeAsync(12_100)
    expect(controller.isActive).toBe(false)
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ kind: 'runtime', message: expect.stringContaining('Chưa mở được micro') }))
    grantMicrophone({ getTracks: () => [{ stop: trackStop }] })
    await pending
    expect(trackStop).toHaveBeenCalledOnce()
    controller.stop()
  })

  it('resumes suspended browser audio before detecting microphone speech', async () => {
    vi.useFakeTimers()
    const startedAt = Date.now()
    const onAudio = vi.fn()
    const onLevel = vi.fn()
    const analyser = {
      fftSize: 32,
      getByteTimeDomainData(data: Uint8Array) {
        data.fill(context.state === 'running' && Date.now() - startedAt < 200 ? 255 : 128)
      },
    }
    const context = {
      ...fakeAudioContext(analyser),
      state: 'suspended',
      resume: vi.fn(async () => { context.state = 'running' }),
    }
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      preferRecorder: true,
      getUserMedia: async () => fakeStream(),
      MediaRecorder: FakeRecorder,
      createAudioContext: () => context,
      onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(), onAudio, onLevel,
    })
    await controller.start()
    await vi.advanceTimersByTimeAsync(1_000)
    expect(context.resume).toHaveBeenCalledOnce()
    expect(onAudio).toHaveBeenCalledOnce()
    expect(onLevel).toHaveBeenCalledWith(1)
    controller.stop()
  })

  it('submits uninterrupted speech at the maximum segment duration', async () => {
    vi.useFakeTimers()
    const onAudio = vi.fn()
    const analyser = { fftSize: 32, getByteTimeDomainData: (data: Uint8Array) => { data.fill(255) } }
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true,
      getUserMedia: async () => fakeStream(), MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext(analyser),
      onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(), onAudio,
    })
    await controller.start()
    await vi.advanceTimersByTimeAsync(12_100)
    expect(onAudio).toHaveBeenCalledOnce()
    controller.stop()
  })

  it('starts SpeechRecognition in the selected locale and emits final transcripts', async () => {
    const recognition = new FakeRecognition()
    const onTranscript = vi.fn()
    const onState = vi.fn()
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getRecognition: () => FakeRecognition,
      createRecognition: () => recognition,
      onTranscript,
      onAudio: vi.fn(),
      onState,
      onError: vi.fn(),
    })

    await controller.start()
    expect(recognition.start).toHaveBeenCalledOnce()
    expect(recognition.lang).toBe('vi-VN')
    expect(recognition.continuous).toBe(true)
    expect(recognition.interimResults).toBe(true)
    expect(recognition.maxAlternatives).toBe(1)

    recognition.onresult?.({
      resultIndex: 0,
      results: [{ isFinal: true, 0: { transcript: '  Xin chào Sky  ' } }],
    })

    expect(onTranscript).toHaveBeenCalledWith('Xin chào Sky')
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({ mode: 'idle', phase: 'idle' }))
  })

  it('uses the recorder fallback, detects speech and emits one segment after silence', async () => {
    vi.useFakeTimers()
    const onAudio = vi.fn()
    const onState = vi.fn()
    const samples = [false, true, true, true, false, false, false, false, false, false, false, false, false, false, false, false, false, false, false]
    let sampleIndex = 0
    const analyser = {
      fftSize: 32,
      getByteTimeDomainData(data: Uint8Array) {
        data.fill(samples[sampleIndex++] ? 255 : 128)
      },
    }
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getRecognition: () => null,
      getUserMedia: vi.fn(async () => fakeStream()),
      MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext(analyser),
      onTranscript: vi.fn(),
      onAudio,
      onState,
      onError: vi.fn(),
      now: () => Date.now(),
    })

    await controller.start()
    await vi.advanceTimersByTimeAsync(850)

    expect(onAudio).toHaveBeenCalledOnce()
    expect(onAudio.mock.calls[0][0]).toBeInstanceOf(Blob)
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({ mode: 'recorder', phase: 'recording' }))
    expect(FakeRecorder.lastTimeslice).toBe(100)
  })

  it('detects quieter speech after adapting to the microphone noise floor', async () => {
    vi.useFakeTimers()
    const onAudio = vi.fn()
    const samples = [false, true, true, true, true, true, true, false, false, false, false, false, false, false, false, false, false, false, false, false, false]
    let sampleIndex = 0
    const analyser = {
      fftSize: 32,
      getByteTimeDomainData(data: Uint8Array) {
        const speechDetected = samples[sampleIndex++] ?? false
        if (!speechDetected) {
          data.fill(128)
          return
        }
        for (let index = 0; index < data.length; index += 1) {
          data[index] = 128 + Math.round(3 * Math.sin((2 * Math.PI * 2 * index) / data.length))
        }
      },
    }
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getRecognition: () => null,
      getUserMedia: vi.fn(async () => fakeStream()),
      MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext(analyser),
      onTranscript: vi.fn(),
      onAudio,
      onState: vi.fn(),
      onError: vi.fn(),
      now: () => Date.now(),
    })

    await controller.start()
    await vi.advanceTimersByTimeAsync(1_200)

    expect(onAudio).toHaveBeenCalledOnce()
    controller.stop()
  })

  it('drops a very short noise segment and keeps the microphone stream open', async () => {
    vi.useFakeTimers()
    const onAudio = vi.fn()
    const getUserMedia = vi.fn(async () => fakeStream())
    const samples = [false, true, false]
    let sampleIndex = 0
    const analyser = {
      fftSize: 32,
      getByteTimeDomainData(data: Uint8Array) {
        data.fill(samples[sampleIndex++] ? 255 : 128)
      },
    }
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getRecognition: () => null,
      getUserMedia,
      MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext(analyser),
      onTranscript: vi.fn(),
      onAudio,
      onState: vi.fn(),
      onError: vi.fn(),
      now: () => Date.now(),
    })

    await controller.start()
    await vi.advanceTimersByTimeAsync(900)

    expect(onAudio).not.toHaveBeenCalled()
    expect(getUserMedia).toHaveBeenCalledOnce()
    expect(controller.mode).toBe('recorder')
    expect(controller.isActive).toBe(true)
    controller.stop()
  })

  it('keeps one utterance alive across a conversational pause before transcribing', async () => {
    vi.useFakeTimers()
    const onAudio = vi.fn()
    const startedAt = Date.now()
    const analyser = {
      fftSize: 32,
      getByteTimeDomainData(data: Uint8Array) {
        const elapsed = Date.now() - startedAt
        const speaking = elapsed < 160 || (elapsed >= 640 && elapsed < 800)
        data.fill(speaking ? 255 : 128)
      },
    }
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getRecognition: () => null,
      getUserMedia: vi.fn(async () => fakeStream()),
      MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext(analyser),
      onTranscript: vi.fn(),
      onAudio,
      onState: vi.fn(),
      onError: vi.fn(),
      now: () => Date.now(),
    })

    await controller.start()
    await vi.advanceTimersByTimeAsync(640)

    expect(onAudio).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1_000)
    expect(onAudio).toHaveBeenCalledOnce()
  })

  it('emits a short segment when the user manually stops capture', async () => {
    vi.useFakeTimers()
    const onAudio = vi.fn()
    const samples = [false, true, false]
    let sampleIndex = 0
    const analyser = {
      fftSize: 32,
      getByteTimeDomainData(data: Uint8Array) {
        data.fill(samples[sampleIndex++] ? 255 : 128)
      },
    }
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getRecognition: () => null,
      getUserMedia: vi.fn(async () => fakeStream()),
      MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext(analyser),
      onTranscript: vi.fn(),
      onAudio,
      onState: vi.fn(),
      onError: vi.fn(),
      now: () => Date.now(),
    })

    await controller.start()
    await vi.advanceTimersByTimeAsync(80)
    controller.stopCapture()

    expect(onAudio).toHaveBeenCalledOnce()
  })

  it('falls back to the recorder when browser recognition reports a network error', async () => {
    vi.useFakeTimers()
    const recognition = new FakeRecognition()
    const getUserMedia = vi.fn(async () => fakeStream())
    const analyser = {
      fftSize: 32,
      getByteTimeDomainData(data: Uint8Array) { data.fill(128) },
    }
    const onError = vi.fn()
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getRecognition: () => FakeRecognition,
      createRecognition: () => recognition,
      getUserMedia,
      MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext(analyser),
      onTranscript: vi.fn(),
      onAudio: vi.fn(),
      onState: vi.fn(),
      onError,
    })

    await controller.start()
    recognition.onerror?.({ error: 'network' })
    await vi.waitFor(() => expect(getUserMedia).toHaveBeenCalledWith({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    }))

    expect(controller.mode).toBe('recorder')
    expect(controller.isActive).toBe(true)
    expect(onError).not.toHaveBeenCalled()
    controller.stop()
  })

  it('prefers the recorder when configured even if browser recognition exists', async () => {
    const recognition = new FakeRecognition()
    const getUserMedia = vi.fn(async () => fakeStream())
    const analyser = {
      fftSize: 32,
      getByteTimeDomainData(data: Uint8Array) { data.fill(128) },
    }
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      preferRecorder: true,
      getRecognition: () => FakeRecognition,
      createRecognition: () => recognition,
      getUserMedia,
      MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext(analyser),
      onTranscript: vi.fn(),
      onAudio: vi.fn(),
      onState: vi.fn(),
      onError: vi.fn(),
    })

    await controller.start()

    expect(recognition.start).not.toHaveBeenCalled()
    expect(getUserMedia).toHaveBeenCalledOnce()
    expect(controller.mode).toBe('recorder')
    controller.stop()
  })

  it('falls back to the recorder when native recognition does not start promptly', async () => {
    vi.useFakeTimers()
    const getUserMedia = vi.fn(async () => fakeStream())
    const analyser = {
      fftSize: 32,
      getByteTimeDomainData(data: Uint8Array) { data.fill(128) },
    }
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getRecognition: () => DelayedRecognition,
      createRecognition: () => new DelayedRecognition(),
      getUserMedia,
      MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext(analyser),
      onTranscript: vi.fn(),
      onAudio: vi.fn(),
      onState: vi.fn(),
      onError: vi.fn(),
    })

    await controller.start()
    await vi.advanceTimersByTimeAsync(650)

    expect(getUserMedia).toHaveBeenCalled()
    expect(controller.mode).toBe('recorder')
    expect(controller.isActive).toBe(true)
    controller.stop()
  })

  it('requests browser echo cancellation for recorder fallback audio', async () => {
    const getUserMedia = vi.fn(async () => fakeStream())
    const analyser = {
      fftSize: 32,
      getByteTimeDomainData(data: Uint8Array) { data.fill(128) },
    }
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getRecognition: () => null,
      getUserMedia,
      MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext(analyser),
      onTranscript: vi.fn(),
      onAudio: vi.fn(),
      onState: vi.fn(),
      onError: vi.fn(),
    })

    await controller.start()

    expect(getUserMedia).toHaveBeenCalledWith({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    })
    controller.stop()
  })

  it('does not restart recognition after an explicit pause', async () => {
    const recognition = new FakeRecognition()
    const controller = new VoiceConversationController({
      locale: 'en-US',
      getRecognition: () => FakeRecognition,
      createRecognition: () => recognition,
      onTranscript: vi.fn(),
      onAudio: vi.fn(),
      onState: vi.fn(),
      onError: vi.fn(),
    })

    await controller.start()
    controller.pause()
    recognition.onend?.()

    expect(recognition.start).toHaveBeenCalledOnce()
  })

  it('becomes inactive after the microphone fallback is unsupported', async () => {
    const onError = vi.fn()
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getRecognition: () => null,
      onTranscript: vi.fn(),
      onAudio: vi.fn(),
      onState: vi.fn(),
      onError,
    })

    await controller.start()

    expect(controller.isActive).toBe(false)
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ kind: 'unsupported' }))
  })

  it('terminates sustained stationary noise early without uploading it', async () => {
    vi.useFakeTimers()
    const onAudio = vi.fn()
    const onState = vi.fn()
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, validateSpeechEnvelope: true,
      getUserMedia: async () => fakeStream(), MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext({
        fftSize: 32, getByteTimeDomainData: (data) => data.fill(170),
      }),
      onTranscript: vi.fn(), onState, onError: vi.fn(), onAudio, now: () => Date.now(),
    })
    await controller.start()
    await vi.advanceTimersByTimeAsync(1_100)
    expect(onAudio).not.toHaveBeenCalled()
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({ phase: 'listening' }))
    expect(controller.isActive).toBe(true)
    controller.stop()
  })

  it('does not start a second microphone while already listening', async () => {
    const getUserMedia = vi.fn(async () => fakeStream())
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true,
      getUserMedia, MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext({
        fftSize: 32, getByteTimeDomainData: (data) => data.fill(128),
      }),
      onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(), onAudio: vi.fn(),
    })
    await controller.start()
    await controller.resume()
    expect(getUserMedia).toHaveBeenCalledTimes(1)
    controller.stop()
  })

  it('does not send stationary fan noise to STT when the evidence guard is enabled', async () => {
    vi.useFakeTimers()
    const onAudio = vi.fn()
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, validateSpeechEnvelope: true,
      getUserMedia: async () => fakeStream(), MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext({
        fftSize: 32, getByteTimeDomainData: (data) => data.fill(170),
      }),
      onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(), onAudio,
      now: () => Date.now(),
    })
    await controller.start()
    await vi.advanceTimersByTimeAsync(12_300)
    expect(onAudio).not.toHaveBeenCalled()
    controller.stop()
  })

  it('still emits varied speech and drops stationary noise in subsequent turns', async () => {
    vi.useFakeTimers()
    const onAudio = vi.fn()
    const wave = [128, 149, 184, 220, 170, 205, 235, 179]
    let index = 0
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, validateSpeechEnvelope: true,
      getUserMedia: async () => fakeStream(), MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext({
        fftSize: 32,
        getByteTimeDomainData: (data) => data.fill(wave[index++] ?? 128),
      }),
      onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(), onAudio,
      now: () => Date.now(),
    })
    await controller.start()
    await vi.advanceTimersByTimeAsync(1_100)
    expect(onAudio).toHaveBeenCalledOnce()
    controller.stop()
  })

  it('manual capture can emit a brief utterance despite the automatic noise guard', async () => {
    vi.useFakeTimers()
    const onAudio = vi.fn()
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, validateSpeechEnvelope: true,
      getUserMedia: async () => fakeStream(), MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext({
        fftSize: 32, getByteTimeDomainData: (data) => data.fill(230),
      }),
      onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(), onAudio,
      now: () => Date.now(),
    })
    await controller.start()
    await vi.advanceTimersByTimeAsync(80)
    controller.stopCapture()
    expect(onAudio).toHaveBeenCalledOnce()
  })

})
