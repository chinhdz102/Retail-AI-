import { describe, expect, it } from 'vitest'
import { FrameProcessor, Message } from '@ricky0123/vad-web'
import { SPEECH_ENDPOINT } from './voice-endpoint'

describe('turn boundaries with a real frame processor', () => {
  it('keeps a one-second pause and the following correction in the same unchanged PCM', async () => {
    const events: Array<{ msg: Message, audio?: Float32Array }> = []
    const processor = new FrameProcessor(async frame => ({
      isSpeech: frame[0] ? .99 : .01, notSpeech: frame[0] ? .01 : .99,
    }), () => {}, SPEECH_ENDPOINT, 32)
    processor.resume()
    const frames = [
      ...Array.from({ length: 20 }, () => new Float32Array(512).fill(.1)),
      ...Array.from({ length: 32 }, () => new Float32Array(512)),
      ...Array.from({ length: 15 }, () => new Float32Array(512).fill(.2)),
    ]
    for (const frame of frames) await processor.process(frame, event => events.push(event))
    expect(events.filter(e => e.msg === Message.SpeechEnd)).toHaveLength(0)
    for (let i = 0; i < 45; i++) {
      const frame = new Float32Array(512)
      frames.push(frame)
      await processor.process(frame, event => events.push(event))
    }
    const completed = events.filter(e => e.msg === Message.SpeechEnd)
    expect(completed).toHaveLength(1)
    const expected = new Float32Array(frames.length * 512)
    frames.forEach((frame, i) => expected.set(frame, i * 512))
    expect(completed[0]?.audio).toEqual(expected)
  })
})
