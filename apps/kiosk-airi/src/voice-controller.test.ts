import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  VoiceConversationController,
  type AudioContextLike,
  type MediaRecorderLike,
  type MediaStreamLike,
  type SpeechRecognitionLike,
} from './voice-controller'
import type { NeuralVadSession } from './voice-neural'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

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
    const onCapture = vi.fn()
    const destroy = vi.fn().mockResolvedValue(undefined)
    let callbacks!: import('./voice-neural').NeuralVadCallbacks
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferNeuralVad: true,
      getUserMedia: async () => ({ getTracks: () => [{ stop: trackStop, label: 'USB mic',
        getSettings: () => ({ sampleRate: 48000, echoCancellation: true, deviceId: 'private' }),
      }] }),
      createNeuralVad: async (value) => {
        callbacks = value
        return { start: vi.fn().mockResolvedValue(undefined), destroy }
      },
      onAudio, onCapture, onTranscript: vi.fn(), onState, onError: vi.fn(),
    })
    await controller.start()
    expect(controller.phase).toBe('listening')
    callbacks.onVADMisfire()
    expect(onAudio).not.toHaveBeenCalled()
    callbacks.onSpeechStart()
    expect(controller.phase).toBe('listening')
    callbacks.onVADMisfire()
    expect(onState).not.toHaveBeenCalledWith(expect.objectContaining({ phase: 'recording' }))
    callbacks.onSpeechStart()
    callbacks.onSpeechRealStart()
    expect(controller.phase).toBe('recording')
    callbacks.onSpeechEnd(new Float32Array(8_000).fill(0.24))
    expect(onAudio).toHaveBeenCalledOnce()
    expect(onCapture).toHaveBeenCalledWith({ source: 'silero-v6', settings: { sampleRate: 48000, echoCancellation: true } })
    const audio = onAudio.mock.calls[0][0] as Blob
    expect(audio.type).toBe('audio/wav')
    expect(audio.size).toBe(44 + 8_000 * 2)
    await vi.waitFor(() => expect(destroy).toHaveBeenCalledOnce())
    expect(trackStop).toHaveBeenCalledOnce()
    callbacks.onSpeechEnd(new Float32Array(8_000).fill(0.7))
    expect(onAudio).toHaveBeenCalledOnce()
    expect(onCapture).toHaveBeenCalledOnce()
    controller.stop()
  })

  it('stops overlong neural input before uploading any truncated audio', async () => {
    const onAudio = vi.fn()
    const onError = vi.fn()
    let now = 0
    let callbacks!: import('./voice-neural').NeuralVadCallbacks
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferNeuralVad: true,
      getUserMedia: async () => fakeStream(),
      createNeuralVad: async (hooks) => {
        callbacks = hooks
        return { start: vi.fn().mockResolvedValue(undefined), destroy: vi.fn().mockResolvedValue(undefined) }
      },
      onAudio, onTranscript: vi.fn(), onState: vi.fn(), onError, now: () => now,
    })
    await controller.start()
    callbacks.onSpeechStart()
    now = 12_200
    callbacks.onFrameProcessed({ isSpeech: 0.95 }, new Float32Array(512).fill(0.2))
    expect(onAudio).not.toHaveBeenCalled()
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'runtime', message: expect.stringContaining('12 giây'),
    }))
    expect(controller.isActive).toBe(false)
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
    const onCapture = vi.fn()
    const destroy = vi.fn().mockResolvedValue(undefined)
    let callbacks!: import('./voice-neural').NeuralVadCallbacks
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferNeuralVad: true,
      getUserMedia: async () => fakeStream(),
      createNeuralVad: async (value) => {
        callbacks = value
        return { start: vi.fn().mockResolvedValue(undefined), destroy }
      },
      onAudio, onCapture, onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(),
    })
    await controller.start()
    controller.pause()
    callbacks.onSpeechEnd(new Float32Array(8_000).fill(0.4))
    expect(onAudio).not.toHaveBeenCalled()
    expect(onCapture).not.toHaveBeenCalled()
    await vi.waitFor(() => expect(destroy).toHaveBeenCalledOnce())
    controller.stop()
  })

  it.each(['factory', 'start'] as const)('keeps the new microphone when an old neural %s resolves', async (stage) => {
    const pendingFactory = deferred<NeuralVadSession>()
    const pendingStart = deferred<void>()
    const oldDestroy = vi.fn().mockResolvedValue(undefined)
    const newDestroy = vi.fn().mockResolvedValue(undefined)
    const newTrackStop = vi.fn()
    const oldVad = { start: vi.fn(() => stage === 'start' ? pendingStart.promise : undefined), destroy: oldDestroy }
    const factory = vi.fn()
      .mockImplementationOnce(() => stage === 'factory' ? pendingFactory.promise : Promise.resolve(oldVad))
      .mockResolvedValue({ start: vi.fn(), destroy: newDestroy })
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferNeuralVad: true,
      getUserMedia: vi.fn().mockResolvedValueOnce(fakeStream())
        .mockResolvedValue({ getTracks: () => [{ stop: newTrackStop }] }),
      createNeuralVad: factory,
      onAudio: vi.fn(), onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(),
    })
    const oldStart = controller.start()
    await vi.waitFor(() => expect(stage === 'factory' ? factory : oldVad.start).toHaveBeenCalled())
    controller.pause()
    await controller.start()
    pendingFactory.resolve(oldVad)
    pendingStart.resolve()
    await oldStart
    expect(controller.phase).toBe('listening')
    expect(controller.isActive).toBe(true)
    expect(newTrackStop).not.toHaveBeenCalled()
    expect(newDestroy).not.toHaveBeenCalled()
    await vi.waitFor(() => expect(oldDestroy).toHaveBeenCalledOnce())
    controller.stop()
  })

  it.each(['neural', 'worklet'] as const)('retains the new startup watchdog when old %s initialization rejects', async (mode) => {
    vi.useFakeTimers()
    const oldInit = deferred<any>()
    const newInit = deferred<any>()
    const onError = vi.fn()
    const pending = vi.fn().mockReturnValueOnce(oldInit.promise).mockReturnValueOnce(newInit.promise)
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferNeuralVad: mode === 'neural', preferPcmWorklet: true,
      getUserMedia: async () => fakeStream(), MediaRecorder: FakeRecorder,
      createNeuralVad: mode === 'neural' ? pending : undefined,
      createAudioContext: () => ({
        ...fakeAudioContext({ fftSize: 32, getByteTimeDomainData: (data) => data.fill(128) }),
        sampleRate: 16000, destination: {}, audioWorklet: { addModule: pending },
      }),
      onAudio: vi.fn(), onTranscript: vi.fn(), onState: vi.fn(), onError,
    })
    const oldStart = controller.start()
    await vi.advanceTimersByTimeAsync(0)
    controller.pause()
    const newStart = controller.start()
    await vi.advanceTimersByTimeAsync(0)
    oldInit.reject(new Error('old initialization failed'))
    await oldStart
    await vi.advanceTimersByTimeAsync(12_001)
    expect(onError).toHaveBeenCalledOnce()
    expect(controller.isActive).toBe(false)
    newInit.reject(new Error('late initialization failed'))
    await newStart
    controller.stop()
  })

  it('does not close a new audio context when an old worklet module finishes loading', async () => {
    const oldModule = deferred<void>()
    const newTrackStop = vi.fn()
    const makeContext = () => ({
      ...fakeAudioContext({ fftSize: 32, getByteTimeDomainData: (data) => data.fill(128) }),
      sampleRate: 16000, destination: {},
      audioWorklet: { addModule: vi.fn().mockResolvedValue(undefined) },
    })
    const oldContext = makeContext()
    oldContext.audioWorklet.addModule.mockReturnValue(oldModule.promise)
    const newContext = makeContext()
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferPcmWorklet: true, MediaRecorder: FakeRecorder,
      getUserMedia: vi.fn().mockResolvedValueOnce(fakeStream())
        .mockResolvedValue({ getTracks: () => [{ stop: newTrackStop }] }),
      createAudioContext: vi.fn().mockReturnValueOnce(oldContext).mockReturnValue(newContext),
      createPcmWorkletNode: () => ({ port: { onmessage: null }, connect: vi.fn(), disconnect: vi.fn() }),
      onAudio: vi.fn(), onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(),
    })
    const oldStart = controller.start()
    await vi.waitFor(() => expect(oldContext.audioWorklet.addModule).toHaveBeenCalled())
    controller.pause()
    await controller.start()
    oldModule.resolve()
    await oldStart
    expect(controller.isActive).toBe(true)
    expect(newContext.close).not.toHaveBeenCalled()
    expect(newTrackStop).not.toHaveBeenCalled()
    controller.stop()
  })

  it('releases the microphone when the AudioContext constructor fails', async () => {
    const stop = vi.fn()
    const onError = vi.fn()
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, MediaRecorder: FakeRecorder,
      getUserMedia: async () => ({ getTracks: () => [{ stop }] }),
      createAudioContext: () => { throw new Error('AudioContext allocation failed') },
      onAudio: vi.fn(), onTranscript: vi.fn(), onState: vi.fn(), onError,
    })
    await controller.start()
    expect(stop).toHaveBeenCalledOnce()
    expect(onError).toHaveBeenCalledOnce()
    expect(controller.isActive).toBe(false)
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
      channelCount: { ideal: 1 }, sampleRate: { ideal: 48000 }, sampleSize: { ideal: 16 }, voiceIsolation: true,
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

  it('rejects overlong fallback speech rather than submitting a partial request', async () => {
    vi.useFakeTimers()
    const onAudio = vi.fn()
    const onError = vi.fn()
    const analyser = { fftSize: 32, getByteTimeDomainData: (data: Uint8Array) => { data.fill(255) } }
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true,
      getUserMedia: async () => fakeStream(), MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext(analyser),
      onTranscript: vi.fn(), onState: vi.fn(), onError, onAudio,
    })
    await controller.start()
    await vi.advanceTimersByTimeAsync(12_100)
    expect(onAudio).not.toHaveBeenCalled()
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'runtime', message: expect.stringContaining('12 giây'),
    }))
    controller.stop()
  })

  it('still releases the microphone when AudioContext close rejects', async () => {
    const trackStop = vi.fn()
    const context = fakeAudioContext({ fftSize: 32, getByteTimeDomainData: data => data.fill(128) })
    context.close = vi.fn().mockRejectedValue(new Error('device already closed'))
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true,
      getUserMedia: async () => ({ getTracks: () => [{ stop: trackStop }] }),
      MediaRecorder: FakeRecorder, createAudioContext: () => context,
      onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(), onAudio: vi.fn(),
    })
    await controller.start()
    controller.stop()
    await Promise.resolve()
    expect(trackStop).toHaveBeenCalledOnce()
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
      results: [{ isFinal: true, 0: { transcript: '  Xin chào Mai  ' } }],
    })

    expect(onTranscript).toHaveBeenCalledWith('Xin chào Mai')
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
        channelCount: { ideal: 1 }, sampleRate: { ideal: 48000 }, sampleSize: { ideal: 16 }, voiceIsolation: true,
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
        channelCount: { ideal: 1 }, sampleRate: { ideal: 48000 }, sampleSize: { ideal: 16 }, voiceIsolation: true,
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

  it('guards neural VAD barge-in during AEC grace window and weak echo, then interrupts on real speech', async () => {
    let now = 1_000
    const onAudio = vi.fn()
    const onBargeIn = vi.fn()
    const onState = vi.fn()
    let callbacks!: import('./voice-neural').NeuralVadCallbacks
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferNeuralVad: true,
      getUserMedia: async () => fakeStream(),
      createNeuralVad: async (hooks) => {
        callbacks = hooks
        return { start: vi.fn().mockResolvedValue(undefined), destroy: vi.fn().mockResolvedValue(undefined) }
      },
      onAudio, onBargeIn, onTranscript: vi.fn(), onState, onError: vi.fn(),
      now: () => now,
    })

    controller.setBargeInMode(true, 450)
    await controller.start()

    // 1) Echo burst inside 450ms grace window is ignored and dropped.
    now = 1_200
    callbacks.onSpeechStart()
    callbacks.onFrameProcessed({ isSpeech: 0.9 }, new Float32Array(512).fill(0.08))
    callbacks.onSpeechRealStart()
    expect(onBargeIn).not.toHaveBeenCalled()
    callbacks.onSpeechEnd(new Float32Array(8_000).fill(0.08))
    expect(onAudio).not.toHaveBeenCalled()

    // 2) Moderate loudspeaker bleed (< 0.065 RMS) after grace window does not interrupt Mai.
    now = 1_600
    callbacks.onSpeechStart()
    callbacks.onFrameProcessed({ isSpeech: 0.82 }, new Float32Array(512).fill(0.045))
    callbacks.onSpeechRealStart()
    expect(onBargeIn).not.toHaveBeenCalled()
    callbacks.onSpeechEnd(new Float32Array(8_000).fill(0.045))
    expect(onAudio).not.toHaveBeenCalled()

    // 3) Real user speaking loudly (>= 0.11 RMS, high speech prob) after grace window confirms barge-in immediately.
    now = 2_000
    callbacks.onSpeechStart()
    callbacks.onFrameProcessed({ isSpeech: 0.95 }, new Float32Array(512).fill(0.12))
    callbacks.onSpeechRealStart()
    expect(onBargeIn).toHaveBeenCalledOnce()
    expect(controller.phase).toBe('recording')

    // 4) Full interrupting utterance is emitted for Whisper transcription.
    callbacks.onSpeechEnd(new Float32Array(8_000).fill(0.12))
    expect(onAudio).toHaveBeenCalledOnce()
    controller.stop()
  })

  it('drops steady fan noise in neural VAD listening mode without emitting false audio', async () => {
    let now = 1_000
    const onAudio = vi.fn()
    const onSpeechMisfire = vi.fn()
    let callbacks!: import('./voice-neural').NeuralVadCallbacks
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferNeuralVad: true,
      getUserMedia: async () => fakeStream(),
      createNeuralVad: async (hooks) => {
        callbacks = hooks
        return { start: vi.fn().mockResolvedValue(undefined), destroy: vi.fn().mockResolvedValue(undefined) }
      },
      onAudio, onSpeechMisfire, onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(),
      now: () => now,
    })

    await controller.start()
    // Calibrate continuous desk fan floor (~0.020 RMS) during silence.
    for (let i = 0; i < 8; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.1 }, new Float32Array(512).fill(0.020))
    }

    // Fan gust briefly fools Silero VAD at 0.022 RMS (still flat / near noise floor).
    callbacks.onSpeechStart()
    for (let i = 0; i < 14; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.74 }, new Float32Array(512).fill(0.022))
    }
    callbacks.onSpeechRealStart()
    callbacks.onSpeechEnd(new Float32Array(8_000).fill(0.022))
    expect(onAudio).not.toHaveBeenCalled()
    expect(onSpeechMisfire).toHaveBeenCalled()
    controller.stop()
  })

  it('confirms fallback recorder barge-in only after grace window and sustained speech', async () => {
    vi.useFakeTimers()
    const onAudio = vi.fn()
    const onBargeIn = vi.fn()
    const startedAt = Date.now()
    const analyser = {
      fftSize: 32,
      getByteTimeDomainData(data: Uint8Array) {
        const elapsed = Date.now() - startedAt
        // Loudspeaker burst during first 300ms (ignored), then real user speech from 600ms to 920ms.
        const speaking = elapsed < 260 || (elapsed >= 600 && elapsed < 920)
        data.fill(speaking ? 235 : 128)
      },
    }
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true,
      getUserMedia: async () => fakeStream(), MediaRecorder: FakeRecorder,
      createAudioContext: () => fakeAudioContext(analyser),
      onAudio, onBargeIn, onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(),
      now: () => Date.now(),
    })

    controller.setBargeInMode(true, 450)
    await controller.start()

    // During grace window: never interrupts.
    await vi.advanceTimersByTimeAsync(400)
    expect(onBargeIn).not.toHaveBeenCalled()
    expect(onAudio).not.toHaveBeenCalled()

    // Sustained user speech after grace window confirms barge-in and emits full segment after pause.
    await vi.advanceTimersByTimeAsync(1_300)
    expect(onBargeIn).toHaveBeenCalledOnce()
    expect(onAudio).toHaveBeenCalledOnce()
    controller.stop()
  })

  it('rejects calibrated loud speaker bleed while allowing normal human barge-in when AEC is clean', async () => {
    let now = 1_000
    let playbackRms = 0.28
    const onAudio = vi.fn()
    const onBargeIn = vi.fn(() => { playbackRms = 0 })
    const onSpeechMisfire = vi.fn()
    let callbacks!: import('./voice-neural').NeuralVadCallbacks
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferNeuralVad: true,
      getPlaybackLevel: () => playbackRms,
      getUserMedia: async () => fakeStream(),
      createNeuralVad: async (hooks) => {
        callbacks = hooks
        return { start: vi.fn().mockResolvedValue(undefined), destroy: vi.fn().mockResolvedValue(undefined) }
      },
      onAudio, onBargeIn, onSpeechMisfire, onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(),
      now: () => now,
    })

    controller.setBargeInMode(true, 450)
    await controller.start()

    // Calibrate loud speaker bleed (~0.085 RMS on mic) during the 450ms grace window.
    for (let i = 0; i < 14; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.85 }, new Float32Array(512).fill(0.085))
    }

    // Zero-frame onset in barge-in mode never bypasses confirmation.
    now = 1_600
    callbacks.onSpeechStart()
    callbacks.onSpeechRealStart()
    expect(onBargeIn).not.toHaveBeenCalled()
    callbacks.onSpeechEnd(new Float32Array(8_000).fill(0.085))
    expect(onAudio).not.toHaveBeenCalled()

    // Loud Mai loudspeaker bleed (~0.09 RMS) after grace must NOT trigger barge-in.
    callbacks.onSpeechStart()
    for (let i = 0; i < 10; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.96 }, new Float32Array(512).fill(0.09))
      if (i === 2) callbacks.onSpeechRealStart()
    }
    expect(onBargeIn).not.toHaveBeenCalled()
    callbacks.onSpeechEnd(new Float32Array(8_000).fill(0.09))
    expect(onAudio).not.toHaveBeenCalled()

    // Genuine human interruption (~0.15 RMS, with a realistic 1-frame Vietnamese consonant dip)
    // interrupts Mai cleanly in ~190ms even before Silero's 320ms onSpeechRealStart fires.
    callbacks.onSpeechStart()
    const humanPattern = [0.15, 0.16, 0.15, 0.03, 0.16, 0.15, 0.16]
    for (const level of humanPattern) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: level > 0.05 ? 0.92 : 0.4 }, new Float32Array(512).fill(level))
    }
    expect(onBargeIn).toHaveBeenCalledOnce()
    // Human finishes short interrupting phrase after Mai stops.
    for (let i = 0; i < 8; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.9 }, new Float32Array(512).fill(0.11))
    }
    callbacks.onSpeechEnd(new Float32Array(9_000).fill(0.11))
    expect(onAudio).toHaveBeenCalledOnce()
    controller.stop()
  })

  it('allows normal-volume human voice (~0.08 RMS) to interrupt even when digital TTS RMS is high', async () => {
    let now = 1_000
    let playbackRms = 0.30 // Loud studio-normalized TTS MP3, suppressed by Chrome AEC on the mic
    const onAudio = vi.fn()
    const onBargeIn = vi.fn(() => { playbackRms = 0 })
    let callbacks!: import('./voice-neural').NeuralVadCallbacks
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferNeuralVad: true,
      getPlaybackLevel: () => playbackRms,
      getUserMedia: async () => fakeStream(),
      createNeuralVad: async (hooks) => {
        callbacks = hooks
        return { start: vi.fn().mockResolvedValue(undefined), destroy: vi.fn().mockResolvedValue(undefined) }
      },
      onAudio, onBargeIn, onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(),
      now: () => now,
    })

    controller.setBargeInMode(true, 450)
    await controller.start()

    // Grace period: Chrome AEC keeps actual mic bleed low (~0.015 RMS).
    for (let i = 0; i < 14; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.2 }, new Float32Array(512).fill(0.015))
    }

    // Shopper speaks normally at 0.08 RMS ("Khoan đã...") to interrupt Mai.
    now = 1_600
    callbacks.onSpeechStart()
    for (let i = 0; i < 6; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.90 }, new Float32Array(512).fill(0.08))
    }
    expect(onBargeIn).toHaveBeenCalledOnce()
    for (let i = 0; i < 8; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.88 }, new Float32Array(512).fill(0.08))
    }
    callbacks.onSpeechEnd(new Float32Array(9_000).fill(0.08))
    expect(onAudio).toHaveBeenCalledOnce()
    controller.stop()
  })

  it('drops lingering Silero segment opened during Mai speech after barge-in mode disarms', async () => {
    let now = 1_000
    const onAudio = vi.fn()
    const onBargeIn = vi.fn()
    const onSpeechStartFrame = vi.fn()
    const onSpeechMisfire = vi.fn()
    let callbacks!: import('./voice-neural').NeuralVadCallbacks
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferNeuralVad: true,
      getPlaybackLevel: () => 0.18,
      getUserMedia: async () => fakeStream(),
      createNeuralVad: async (hooks) => {
        callbacks = hooks
        return { start: vi.fn().mockResolvedValue(undefined), destroy: vi.fn().mockResolvedValue(undefined) }
      },
      onAudio, onBargeIn, onSpeechStartFrame, onSpeechMisfire,
      onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(),
      now: () => now,
    })

    controller.setBargeInMode(true, 450)
    await controller.start()

    // Calibrate speaker bleed (~0.09 RMS) during the grace window.
    for (let i = 0; i < 14; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.85 }, new Float32Array(512).fill(0.09))
    }

    // Mai's loud last word opens Silero VAD without confirming barge-in.
    now = 1_600
    callbacks.onSpeechStart()
    for (let i = 0; i < 5; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.92 }, new Float32Array(512).fill(0.11))
    }
    callbacks.onSpeechRealStart()
    expect(onBargeIn).not.toHaveBeenCalled()

    // Mai finishes speaking and resumeAfterSpeech disarms barge-in after 400ms
    // while Silero's 750ms redemption window is still open.
    now = 2_000
    controller.setBargeInMode(false)
    for (let i = 0; i < 10; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.1 }, new Float32Array(512).fill(0.005))
    }
    callbacks.onSpeechEnd(new Float32Array(8_000).fill(0.11))
    expect(onSpeechStartFrame).not.toHaveBeenCalled()
    expect(onAudio).not.toHaveBeenCalled()
    expect(onSpeechMisfire).toHaveBeenCalled()
    controller.stop()
  })

  it('drops a false barge-in spike when no human keeps speaking after Mai stops', async () => {
    let now = 1_000
    const onAudio = vi.fn()
    const onBargeIn = vi.fn()
    const onSpeechMisfire = vi.fn()
    let callbacks!: import('./voice-neural').NeuralVadCallbacks
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferNeuralVad: true,
      getUserMedia: async () => fakeStream(),
      createNeuralVad: async (hooks) => {
        callbacks = hooks
        return { start: vi.fn().mockResolvedValue(undefined), destroy: vi.fn().mockResolvedValue(undefined) }
      },
      onAudio, onBargeIn, onSpeechMisfire, onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(),
      now: () => now,
    })

    controller.setBargeInMode(true, 450)
    await controller.start()

    // Transient spike triggers barge-in (silencing Mai's audio), then only decay + silence follows.
    now = 2_000
    callbacks.onSpeechStart()
    callbacks.onFrameProcessed({ isSpeech: 0.95 }, new Float32Array(512).fill(0.13))
    callbacks.onSpeechRealStart()
    expect(onBargeIn).toHaveBeenCalledOnce()

    // 5 frames of fading reverb tail + 10 frames of room silence (no human voice).
    for (let i = 0; i < 5; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.7 }, new Float32Array(512).fill(0.03))
    }
    for (let i = 0; i < 10; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.05 }, new Float32Array(512).fill(0.004))
    }
    callbacks.onSpeechEnd(new Float32Array(8_000).fill(0.08))
    expect(onAudio).not.toHaveBeenCalled()
    expect(onSpeechMisfire).toHaveBeenCalled()
    controller.stop()
  })

  it('drops sparse noise clicks and high-ZCR hiss without opening streaming STT', async () => {
    let now = 1_000
    const onAudio = vi.fn()
    const onSpeechStartFrame = vi.fn()
    const onSpeechMisfire = vi.fn()
    let callbacks!: import('./voice-neural').NeuralVadCallbacks
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferNeuralVad: true,
      getUserMedia: async () => fakeStream(),
      createNeuralVad: async (hooks) => {
        callbacks = hooks
        return { start: vi.fn().mockResolvedValue(undefined), destroy: vi.fn().mockResolvedValue(undefined) }
      },
      onAudio, onSpeechStartFrame, onSpeechMisfire,
      onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(),
      now: () => now,
    })

    await controller.start()

    // 1) High-ZCR alternating hiss (air/fan/paper rustle, zcr = 1.0 > 0.42) is rejected.
    const hissFrame = new Float32Array(512)
    for (let i = 0; i < hissFrame.length; i += 1) hissFrame[i] = i % 2 === 0 ? 0.04 : -0.04
    callbacks.onSpeechStart()
    for (let i = 0; i < 12; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.85 }, hissFrame)
      if (i === 9) callbacks.onSpeechRealStart()
    }
    callbacks.onSpeechEnd(new Float32Array(8_000).fill(0.04))
    expect(onSpeechStartFrame).not.toHaveBeenCalled()
    expect(onAudio).not.toHaveBeenCalled()
    expect(onSpeechMisfire).toHaveBeenCalled()

    // 2) Sparse isolated 1-frame clicks scattered across silence are also rejected.
    callbacks.onSpeechStart()
    for (let i = 0; i < 18; i += 1) {
      now += 32
      const click = i === 2 || i === 10
      callbacks.onFrameProcessed(
        { isSpeech: click ? 0.82 : 0.4 },
        new Float32Array(512).fill(click ? 0.05 : 0.005),
      )
      if (i === 10) callbacks.onSpeechRealStart()
    }
    callbacks.onSpeechEnd(new Float32Array(9_000).fill(0.01))
    expect(onSpeechStartFrame).not.toHaveBeenCalled()
    expect(onAudio).not.toHaveBeenCalled()
    controller.stop()
  })

  it('silently resets unconfirmed neutral-zone room noise instead of failing at 12s', async () => {
    let now = 1_000
    const onAudio = vi.fn()
    const onError = vi.fn()
    const onSpeechStartFrame = vi.fn()
    let callbacks!: import('./voice-neural').NeuralVadCallbacks
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferNeuralVad: true,
      getUserMedia: async () => fakeStream(),
      createNeuralVad: async (hooks) => {
        callbacks = hooks
        return { start: vi.fn().mockResolvedValue(undefined), destroy: vi.fn().mockResolvedValue(undefined) }
      },
      onAudio, onError, onSpeechStartFrame,
      onTranscript: vi.fn(), onState: vi.fn(),
      now: () => now,
    })

    await controller.start()
    callbacks.onSpeechStart()
    // Soft room noise stays stuck in Silero neutral zone for 3 seconds without real speech.
    for (let i = 0; i < 95; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.48 }, new Float32Array(512).fill(0.009))
    }
    expect(onError).not.toHaveBeenCalled()
    expect(onSpeechStartFrame).not.toHaveBeenCalled()
    expect(onAudio).not.toHaveBeenCalled()
    expect(controller.isActive).toBe(true)
    controller.stop()
  })

  it('holds and merges a mid-sentence pause when Smart Turn signals an incomplete clause', async () => {
    vi.useFakeTimers()
    let now = 1_000
    const onAudio = vi.fn()
    const onSpeechStartFrame = vi.fn()
    let holdResponses = [1_000, 0]
    const shouldHoldTurnEnd = vi.fn(() => holdResponses.shift() ?? 0)
    let callbacks!: import('./voice-neural').NeuralVadCallbacks
    const controller = new VoiceConversationController({
      locale: 'vi-VN', preferRecorder: true, preferNeuralVad: true,
      shouldHoldTurnEnd,
      getUserMedia: async () => fakeStream(),
      createNeuralVad: async (hooks) => {
        callbacks = hooks
        return { start: vi.fn().mockResolvedValue(undefined), destroy: vi.fn().mockResolvedValue(undefined) }
      },
      onAudio, onSpeechStartFrame,
      onTranscript: vi.fn(), onState: vi.fn(), onError: vi.fn(),
      now: () => now,
    })

    await controller.start()

    // First half of sentence: "Cho mình hỏi là..."
    callbacks.onSpeechStart()
    for (let i = 0; i < 8; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.9 }, new Float32Array(512).fill(0.08))
      if (i === 5) callbacks.onSpeechRealStart()
    }
    callbacks.onSpeechEnd(new Float32Array(6_400).fill(0.08))
    expect(shouldHoldTurnEnd).toHaveBeenCalledTimes(1)
    expect(onAudio).not.toHaveBeenCalled()
    expect(controller.isActive).toBe(true)

    // User resumes after 400ms pause: "...tương ớt Chin Su giá bao nhiêu?"
    now += 400
    await vi.advanceTimersByTimeAsync(400)
    callbacks.onSpeechStart()
    for (let i = 0; i < 8; i += 1) {
      now += 32
      callbacks.onFrameProcessed({ isSpeech: 0.92 }, new Float32Array(512).fill(0.09))
      if (i === 5) callbacks.onSpeechRealStart()
    }
    callbacks.onSpeechEnd(new Float32Array(6_400).fill(0.09))

    expect(shouldHoldTurnEnd).toHaveBeenCalledTimes(2)
    expect(onAudio).toHaveBeenCalledOnce()
    // Single WS session opened once and merged WAV exceeds both chunks combined.
    expect(onSpeechStartFrame).toHaveBeenCalledOnce()
    const mergedBlob = onAudio.mock.calls[0][0] as Blob
    expect(mergedBlob.size).toBeGreaterThan(44 + 12_800 * 2)
    controller.stop()
    vi.useRealTimers()
  })

})
