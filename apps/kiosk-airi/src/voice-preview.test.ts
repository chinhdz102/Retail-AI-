import { describe, expect, it, vi } from 'vitest'
import { FrameProcessor, Message } from '@ricky0123/vad-web'
import { SPEECH_ENDPOINT } from './voice-endpoint'
import { SpeechPreview, TranscriptionPreview } from './voice-preview'

const frame = (value: number) => ({ frame: new Float32Array([value, value]) })

describe('voice endpoint preview', () => {
  it('overlaps 928ms with endpoint waiting, preserves a 1s pause and the correction', async () => {
    const ready = vi.fn()
    const invalidated = vi.fn()
    const previews = new SpeechPreview({ ready, invalidated })
    const ends: Array<{ audio: Float32Array; preview?: number }> = []
    const processor = new FrameProcessor(async frame => ({
      isSpeech: frame[0] ? .99 : .01, notSpeech: frame[0] ? .01 : .99,
    }), () => {}, SPEECH_ENDPOINT, 32)
    processor.resume()
    const originals: Float32Array[] = []
    async function feed(count: number, value: number) {
      for (let i = 0; i < count; i++) {
        const samples = new Float32Array(512).fill(value)
        originals.push(samples)
        await processor.process(samples, e => {
          if (e.msg === Message.SpeechEnd) ends.push({ audio: e.audio, preview: previews.finish() })
        })
        if (processor.speaking) previews.frame(value ? .99 : .01, processor.audioBuffer, processor.speechRealStartFired)
      }
    }
    await feed(20, .1)
    await feed(16, 0)
    expect(ready).toHaveBeenCalledTimes(1)
    expect(ends).toHaveLength(0)
    const firstId = ready.mock.calls[0]![1]
    await feed(16, 0)
    await feed(15, .2) // "không phải nước giặt" after a 1024ms pause
    expect(invalidated).toHaveBeenCalledWith(firstId)
    expect(ends).toHaveLength(0)
    await feed(16, 0)
    const secondId = ready.mock.calls[1]![1]
    await feed(29, 0)
    expect(ends).toHaveLength(1)
    expect(ends[0]!.preview).toBe(secondId)
    expect(ends[0]!.audio).toEqual(Float32Array.from(originals.flatMap(item => Array.from(item))))
    expect((45 - 16) * 32).toBe(928)
  })

  it('bounds preview requests across many pauses and falls back to the full turn', () => {
    const ready = vi.fn(), invalidated = vi.fn()
    const preview = new SpeechPreview({ ready, invalidated })
    for (let turn = 0; turn < 4; turn++) {
      for (let i = 0; i < 16; i++) preview.frame(.01, [frame(i)], true)
      preview.frame(.15, [frame(1)], true)
    }
    expect(ready).toHaveBeenCalledTimes(2)
    expect(preview.finish()).toBeUndefined()
  })

  it('starts one speculative decode during a confident pause', () => {
    const ready = vi.fn()
    const invalidated = vi.fn()
    const preview = new SpeechPreview({ ready, invalidated })
    for (let i = 0; i < 16; i++) preview.frame(0, [frame(i)], true)
    expect(ready).toHaveBeenCalledTimes(1)
    expect(ready.mock.calls[0]?.[0]).toBeInstanceOf(Float32Array)
    expect(ready.mock.calls[0]?.[1]).toBeTypeOf('number')
    preview.frame(.4, [frame(1)], true)
    expect(invalidated).toHaveBeenCalledTimes(1)
  })

  it('does not preview before confirmed speech or on uncertain noise', () => {
    const ready = vi.fn()
    const preview = new SpeechPreview({ ready, invalidated: vi.fn() })
    for (let i = 0; i < 20; i++) preview.frame(.15, [frame(i)], false)
    for (let i = 0; i < 20; i++) preview.frame(.2, [frame(i)], true)
    expect(ready).not.toHaveBeenCalled()
  })

  it('aborts an invalidated request and only takes the matching final turn', async () => {
    let reject!: (error: Error) => void
    const pending = new Promise<string>((_, fail) => { reject = fail })
    const preview = new TranscriptionPreview<string>()
    preview.start(7, 'session', () => pending)
    preview.cancel(6)
    const taken = preview.take(7, 'session')
    expect(taken).toBeDefined()
    reject(new Error('cancelled'))
    await expect(taken!.result).resolves.toBeNull()
    preview.start(8, 'session', async () => 'ok')
    expect(preview.take(7, 'session')).toBeUndefined()
    expect(preview.take(8, 'session')).toBeUndefined()
  })
})
