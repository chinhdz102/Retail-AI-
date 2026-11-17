import { afterEach, describe, expect, it, vi } from 'vitest'
import { VoiceConversationController } from './voice-controller'
import type { NeuralVadCallbacks } from './voice-neural'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
function setup(media?: Promise<MediaStream>) {
  let callbacks!: NeuralVadCallbacks
  const stop = vi.fn()
  const stream = { getTracks: () => [{ stop }], getAudioTracks: () => [{ label: 'Mic' }] } as unknown as MediaStream
  const onAudio = vi.fn((_audio: Blob) => expect(stop).toHaveBeenCalled())
  const onError = vi.fn()
  const onState = vi.fn()
  const onLevel = vi.fn()
  const onNotice = vi.fn()
  const onSpeechStart = vi.fn()
  const destroy = vi.fn()
  const flush = vi.fn(() => callbacks.onSpeechEnd(new Float32Array(16000)))
  const getMedia = vi.fn(() => media ?? Promise.resolve(stream))
  const createVad = vi.fn(async (options: NeuralVadCallbacks) => {
    callbacks = options
    await options.getStream()
    return { start: vi.fn(), destroy, flush }
  })
  const controller = new VoiceConversationController({
    deviceId: 'physical-mic', createVad, getUserMedia: getMedia, onAudio, onError, onState, onLevel, onNotice, onSpeechStart,
  })
  return { controller, onAudio, onError, onState, onLevel, onNotice, onSpeechStart, stop, stream, destroy, getMedia,
    createVad, flush, callbacks: () => callbacks }
}
afterEach(() => vi.useRealTimers())

describe('Vietnamese neural capture lifecycle', () => {
  it('notifies confirmed onset before WAV completion without releasing buffered audio', async () => {
    const s = setup()
    await s.controller.start()
    s.callbacks().onSpeechStart()
    expect(s.onSpeechStart).not.toHaveBeenCalled()
    s.callbacks().onSpeechRealStart()
    expect(s.onSpeechStart).toHaveBeenCalledTimes(1)
    expect(s.stop).not.toHaveBeenCalled()
    expect(s.onAudio).not.toHaveBeenCalled()
    s.callbacks().onSpeechEnd(new Float32Array([.1, .2, .3]))
    expect(s.onAudio).toHaveBeenCalledTimes(1)
    s.controller.stop()
  })
  it('opens one input with mild browser suppression and without stacked auto gain', async () => {
    const s = setup()
    await Promise.all([s.controller.start(), s.controller.start()])
    expect(s.createVad).toHaveBeenCalledTimes(1)
    expect(s.getMedia).toHaveBeenCalledWith({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false, channelCount: 1,
        deviceId: { exact: 'physical-mic' } }, video: false,
    })
    s.controller.stop()
  })

  it('releases tracks before uploading one WAV and ignores retired callbacks', async () => {
    const s = setup()
    await s.controller.start()
    const cb = s.callbacks()
    cb.onSpeechRealStart()
    cb.onSpeechEnd(new Float32Array([0, .1, -.1]))
    expect(s.onAudio).toHaveBeenCalledTimes(1)
    expect(s.onAudio.mock.calls[0]?.[0]).toMatchObject({ type: 'audio/wav', size: 50 })
    cb.onSpeechEnd(new Float32Array(16000))
    cb.onSpeechRealStart()
    cb.onFrameProcessed({ isSpeech: 1 }, new Float32Array([1]))
    expect(s.onAudio).toHaveBeenCalledTimes(1)
    expect(s.onState.mock.lastCall?.[0].phase).toBe('idle')
    expect(s.onLevel.mock.lastCall?.[0]).toBe(0)
  })

  it('stops a permission request which completes after cancellation', async () => {
    const pending = deferred<MediaStream>()
    const s = setup(pending.promise)
    const start = s.controller.start()
    await Promise.resolve()
    s.controller.stop()
    pending.resolve(s.stream)
    await start
    expect(s.stop).toHaveBeenCalled()
    expect(s.controller.isActive).toBe(false)
    expect(s.onError).not.toHaveBeenCalled()
  })

  it('retires old callbacks across a fresh start', async () => {
    const s = setup()
    await s.controller.start()
    const old = s.callbacks()
    s.controller.pause()
    await s.controller.start()
    old.onSpeechEnd(new Float32Array(100))
    old.onVADMisfire()
    expect(s.onAudio).not.toHaveBeenCalled()
    expect(s.onNotice).not.toHaveBeenCalled()
    expect(s.controller.isActive).toBe(true)
    s.controller.stop()
  })

  it('keeps listening on a misfire without uploading noise', async () => {
    const s = setup()
    await s.controller.start()
    s.callbacks().onSpeechStart()
    s.callbacks().onVADMisfire()
    expect(s.onAudio).not.toHaveBeenCalled()
    expect(s.onState.mock.lastCall?.[0].phase).toBe('listening')
    s.controller.stop()
  })

  it('submits a continuous turn at the duration boundary instead of discarding it', async () => {
    vi.useFakeTimers()
    const s = setup()
    await s.controller.start()
    s.callbacks().onSpeechRealStart()
    await vi.advanceTimersByTimeAsync(45_000)
    expect(s.stop).toHaveBeenCalled()
    expect(s.flush).toHaveBeenCalledTimes(1)
    expect(s.onError).not.toHaveBeenCalled()
    expect(s.onAudio).toHaveBeenCalledTimes(1)
  })

  it('cancels explicit Stop without unexpectedly uploading the cancelled turn', async () => {
    const s = setup()
    await s.controller.start()
    s.callbacks().onSpeechRealStart()
    s.controller.stop()
    expect(s.flush).not.toHaveBeenCalled()
    expect(s.onAudio).not.toHaveBeenCalled()
    expect(s.stop).toHaveBeenCalled()
  })

  it('bounds model and permission startup without reviving a cancelled input', async () => {
    vi.useFakeTimers()
    const permission = deferred<MediaStream>()
    const s = setup(permission.promise)
    const start = s.controller.start()
    await vi.advanceTimersByTimeAsync(12000)
    expect(s.onError).toHaveBeenCalledTimes(1)
    permission.resolve(s.stream)
    await start
    expect(s.stop).toHaveBeenCalled()
    expect(s.controller.isActive).toBe(false)
  })

  it('stops capture on neural runtime failure without sending a partial utterance', async () => {
    const s = setup()
    await s.controller.start()
    s.callbacks().onError(new Error('WASM failed'))
    expect(s.stop).toHaveBeenCalled()
    expect(s.onAudio).not.toHaveBeenCalled()
    expect(s.onError).toHaveBeenCalledTimes(1)
  })
})
