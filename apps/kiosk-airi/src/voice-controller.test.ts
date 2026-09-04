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
  static isTypeSupported = vi.fn(() => true)
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
    const samples = [false, true, true, false, false, false, false, false, false, false, false, false, false, false, false, false, false, false, false]
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
    await vi.advanceTimersByTimeAsync(700)

    expect(onAudio).toHaveBeenCalledOnce()
    expect(onAudio.mock.calls[0][0]).toBeInstanceOf(Blob)
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({ mode: 'recorder', phase: 'recording' }))
    expect(FakeRecorder.lastTimeslice).toBe(100)
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
})
