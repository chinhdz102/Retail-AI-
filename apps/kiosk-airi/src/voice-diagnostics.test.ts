import { afterEach, expect, it, vi } from 'vitest'
import { LocalVoiceDiagnostics } from './voice-diagnostics'
import { captureMetadata } from './voice-controller'

afterEach(() => vi.restoreAllMocks())

it('exports only allowlisted actual capture settings without device identifiers', () => {
  const metadata = captureMetadata({ getTracks: () => [{ stop() {}, getSettings: () => ({
    sampleRate: 48000, channelCount: 1, echoCancellation: true,
    noiseSuppression: false, deviceId: 'private', groupId: 'private-group',
  }) }] }, 'silero-v6')
  expect(metadata).toEqual({ source: 'silero-v6', settings: {
    sampleRate: 48000, channelCount: 1, echoCancellation: true, noiseSuppression: false,
  } })
})

it('releases replaced audio/JSON and blocks a late response after clearing', () => {
  let index = 0
  const create = vi.spyOn(URL, 'createObjectURL').mockImplementation(() => `blob:${++index}`)
  const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  const store = new LocalVoiceDiagnostics()
  const audio = new Blob(['exact bytes'], { type: 'audio/wav' })
  const finishOld = store.begin(audio, 'vi')
  expect(create.mock.calls[0]?.[0]).toBe(audio)
  const finishCurrent = store.begin(audio, 'en')
  expect(revoke.mock.calls.flat()).toEqual(['blob:1', 'blob:2'])
  const response = { session_id: 's', text: 'hello', language: 'en', model: 'whisper' }
  expect(finishOld(response)).toBe(false)
  expect(finishCurrent(response)).toBe(true)
  const currentAudio = store.audioUrl
  const currentJson = store.jsonUrl
  store.clear()
  expect(revoke).toHaveBeenCalledWith(currentAudio)
  expect(revoke).toHaveBeenCalledWith(currentJson)
  const calls = create.mock.calls.length
  expect(finishCurrent(response)).toBe(false)
  expect(create).toHaveBeenCalledTimes(calls)
  expect(store.audioUrl).toBe('')
  expect(store.jsonUrl).toBe('')
})
