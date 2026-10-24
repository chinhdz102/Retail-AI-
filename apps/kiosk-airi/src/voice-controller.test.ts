import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  VoiceConversationController,
  type MediaStreamLike,
  type VoiceVADOptions,
} from './voice-controller'

function fakeStream(label = 'Headset microphone'): MediaStreamLike {
  return { getTracks: () => [{ stop: vi.fn(), label }] }
}

class FakeVAD {
  readonly options: VoiceVADOptions
  readonly start = vi.fn(async () => {
    this.stream = this.stream
      ? await this.options.resumeStream(this.stream)
      : await this.options.getStream()
  })
  readonly pause = vi.fn(async () => {
    if (this.speaking) {
      this.speaking = false
      await this.options.onSpeechEnd(this.pendingAudio)
    }
    if (this.stream) await this.options.pauseStream(this.stream)
    this.stream = null
  })
  readonly destroy = vi.fn(async () => this.pause())
  stream: MediaStream | null = null
  speaking = false
  pendingAudio = new Float32Array(8_000).map((_, index) => (
    0.04 * Math.sin((2 * Math.PI * 180 * index) / 16_000)
  ))

  constructor(options: VoiceVADOptions) {
    this.options = options
  }

  speak(): void {
    this.speaking = true
    this.options.onSpeechStart()
    this.options.onSpeechRealStart()
  }

  finishSpeech(audio = this.pendingAudio): void {
    this.speaking = false
    this.options.onSpeechEnd(audio)
  }

  misfire(): void {
    this.speaking = false
    this.options.onVADMisfire()
  }
}

function fakeVADFactory() {
  let current: FakeVAD | null = null
  const allVADs: FakeVAD[] = []
  const createVAD = vi.fn(async (options: VoiceVADOptions) => {
    current = new FakeVAD(options)
    allVADs.push(current)
    return current
  })
  return {
    createVAD,
    getVADs: () => allVADs,
    getVAD: () => {
      if (!current) throw new Error('VAD has not been initialized')
      return current
    },
  }
}

afterEach(() => vi.useRealTimers())

describe('VoiceConversationController', () => {
  it('uses local Silero VAD with the selected microphone and browser echo/noise controls', async () => {
    const getUserMedia = vi.fn(async () => fakeStream())
    const onInputDevice = vi.fn()
    const onState = vi.fn()
    const { createVAD } = fakeVADFactory()
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      deviceId: 'headset-input',
      getUserMedia,
      createVAD,
      onAudio: vi.fn(),
      onTranscript: vi.fn(),
      onState,
      onError: vi.fn(),
      onInputDevice,
    })

    await controller.start()

    expect(createVAD).toHaveBeenCalledOnce()
    const options = createVAD.mock.calls[0]?.[0]
    expect(options).toMatchObject({
      model: 'v5',
      positiveSpeechThreshold: 0.42,
      negativeSpeechThreshold: 0.28,
      redemptionMs: 1400,
      preSpeechPadMs: 800,
      minSpeechMs: 400,
    })
    expect(options?.baseAssetPath).toContain('/vad/')
    expect(options?.onnxWASMBasePath).toContain('/vad/')
    expect(getUserMedia).toHaveBeenCalledWith({ audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      deviceId: { exact: 'headset-input' },
    } })
    expect(onInputDevice).toHaveBeenCalledWith('Headset microphone')
    expect(onState).toHaveBeenLastCalledWith({ mode: 'recorder', phase: 'listening', permission: 'granted' })
    controller.stop()
  })

  it('sends only a Silero speech segment as 16 kHz mono PCM WAV', async () => {
    const onAudio = vi.fn()
    const { createVAD, getVAD } = fakeVADFactory()
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getUserMedia: async () => fakeStream(),
      createVAD,
      onAudio,
      onTranscript: vi.fn(),
      onState: vi.fn(),
      onError: vi.fn(),
    })

    await controller.start()
    const vad = getVAD()
    vad.speak()
    expect(controller.phase).toBe('recording')
    vad.finishSpeech()
    await vi.waitFor(() => expect(onAudio).toHaveBeenCalledOnce())

    const audio = onAudio.mock.calls[0]?.[0] as Blob
    expect(audio.type).toBe('audio/wav')
    const view = new DataView(await audio.arrayBuffer())
    expect(String.fromCharCode(...new Uint8Array(view.buffer, 0, 4))).toBe('RIFF')
    expect(String.fromCharCode(...new Uint8Array(view.buffer, 8, 4))).toBe('WAVE')
    expect(view.getUint32(24, true)).toBe(16_000)
    expect(view.getUint16(22, true)).toBe(1)
    expect(controller.isActive).toBe(false)
  })

  it('does not upload a Silero VAD misfire and remains ready for speech', async () => {
    const onAudio = vi.fn()
    const onState = vi.fn()
    const { createVAD, getVAD } = fakeVADFactory()
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getUserMedia: async () => fakeStream(),
      createVAD,
      onAudio,
      onTranscript: vi.fn(),
      onState,
      onError: vi.fn(),
    })

    await controller.start()
    const vad = getVAD()
    vad.speak()
    vad.misfire()

    expect(onAudio).not.toHaveBeenCalled()
    expect(controller.isActive).toBe(true)
    expect(onState).toHaveBeenLastCalledWith({ mode: 'recorder', phase: 'listening', permission: 'granted' })
    controller.stop()
  })

  it('drops a near-silent segment before Whisper and resumes hands-free listening', async () => {
    const onAudio = vi.fn()
    const { createVAD, getVAD } = fakeVADFactory()
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getUserMedia: async () => fakeStream(),
      createVAD,
      onAudio,
      onTranscript: vi.fn(),
      onState: vi.fn(),
      onError: vi.fn(),
    })

    await controller.start()
    const vad = getVAD()
    vad.speak()
    vad.finishSpeech(new Float32Array(8_000).fill(0.00004))

    await vi.waitFor(() => expect(createVAD).toHaveBeenCalledTimes(2))
    expect(onAudio).not.toHaveBeenCalled()
    expect(controller.isActive).toBe(true)
    expect(controller.phase).toBe('listening')
    controller.stop()
  })

  it('keeps a quiet speech-level segment above the microphone floor', async () => {
    const onAudio = vi.fn()
    const { createVAD, getVAD } = fakeVADFactory()
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getUserMedia: async () => fakeStream(),
      createVAD,
      onAudio,
      onTranscript: vi.fn(),
      onState: vi.fn(),
      onError: vi.fn(),
    })
    const quietSpeech = new Float32Array(8_000).map((_, index) => (
      0.0012 * Math.sin((2 * Math.PI * 180 * index) / 16_000)
    ))

    await controller.start()
    getVAD().speak()
    getVAD().finishSpeech(quietSpeech)

    await vi.waitFor(() => expect(onAudio).toHaveBeenCalledOnce())
    expect(controller.isActive).toBe(false)
    controller.stop()
  })

  it('reopens the selected microphone after assistant playback pauses listening', async () => {
    const getUserMedia = vi.fn(async () => fakeStream())
    const { createVAD } = fakeVADFactory()
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      deviceId: 'selected-input',
      getUserMedia,
      createVAD,
      onAudio: vi.fn(),
      onTranscript: vi.fn(),
      onState: vi.fn(),
      onError: vi.fn(),
    })

    await controller.start()
    controller.pause()
    await controller.start()

    expect(getUserMedia).toHaveBeenCalledTimes(2)
    expect(getUserMedia).toHaveBeenLastCalledWith({ audio: expect.objectContaining({
      deviceId: { exact: 'selected-input' },
    }) })
    controller.stop()
  })

  it('falls back to the system microphone when a saved device ID is stale', async () => {
    const getUserMedia = vi.fn()
      .mockRejectedValueOnce(Object.assign(new Error('device missing'), { name: 'OverconstrainedError' }))
      .mockResolvedValueOnce(fakeStream('System microphone'))
    const onInputDevice = vi.fn()
    const onState = vi.fn()
    const { createVAD } = fakeVADFactory()
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      deviceId: 'stale-device-id',
      getUserMedia,
      createVAD,
      onAudio: vi.fn(),
      onTranscript: vi.fn(),
      onState,
      onError: vi.fn(),
      onInputDevice,
    })

    await controller.start()

    expect(getUserMedia).toHaveBeenNthCalledWith(1, { audio: expect.objectContaining({
      deviceId: { exact: 'stale-device-id' },
    }) })
    expect(getUserMedia).toHaveBeenNthCalledWith(2, { audio: expect.objectContaining({
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    }) })
    expect(onInputDevice).toHaveBeenCalledWith('System microphone')
    expect(onState).toHaveBeenLastCalledWith({ mode: 'recorder', phase: 'listening', permission: 'granted' })
    controller.stop()
  })

  it('rejects late speech events from a previous microphone generation', async () => {
    const onAudio = vi.fn()
    const { createVAD, getVAD, getVADs } = fakeVADFactory()
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getUserMedia: async () => fakeStream(),
      createVAD,
      onAudio,
      onTranscript: vi.fn(),
      onState: vi.fn(),
      onError: vi.fn(),
    })

    await controller.start()
    const previousVAD = getVAD()
    controller.pause()
    await controller.start()
    const currentVAD = getVAD()

    expect(getVADs()).toHaveLength(2)
    expect(currentVAD).not.toBe(previousVAD)
    previousVAD.finishSpeech()
    await Promise.resolve()
    expect(onAudio).not.toHaveBeenCalled()

    currentVAD.speak()
    currentVAD.finishSpeech()
    await vi.waitFor(() => expect(onAudio).toHaveBeenCalledOnce())
    controller.stop()
  })

  it('flushes a current utterance when the customer manually stops capture', async () => {
    const onAudio = vi.fn()
    const { createVAD, getVAD } = fakeVADFactory()
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getUserMedia: async () => fakeStream(),
      createVAD,
      onAudio,
      onTranscript: vi.fn(),
      onState: vi.fn(),
      onError: vi.fn(),
    })

    await controller.start()
    const vad = getVAD()
    vad.speak()
    controller.stopCapture()
    await vi.waitFor(() => expect(onAudio).toHaveBeenCalledOnce())
    expect(controller.isActive).toBe(false)
  })

  it('shows a visible error if microphone permission is denied', async () => {
    const onError = vi.fn()
    const controller = new VoiceConversationController({
      locale: 'vi-VN',
      getUserMedia: async () => { throw Object.assign(new Error('blocked'), { name: 'NotAllowedError' }) },
      createVAD: async (options) => new FakeVAD(options),
      onAudio: vi.fn(),
      onTranscript: vi.fn(),
      onState: vi.fn(),
      onError,
    })

    await controller.start()

    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ kind: 'permission' }))
    expect(controller.isActive).toBe(false)
  })
})
