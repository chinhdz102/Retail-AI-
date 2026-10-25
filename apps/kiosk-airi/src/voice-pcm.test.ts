import { describe, expect, it } from 'vitest'
import { PcmCaptureBuffer } from './voice-pcm'

describe('PcmCaptureBuffer', () => {
  it('preserves speech onset from the ring buffer and emits a standard WAV header', async () => {
    const capture = new PcmCaptureBuffer(16_000, 350)
    capture.append(new Float32Array(160).fill(0.15))
    capture.append(new Float32Array(160).fill(0.25))
    capture.start()
    capture.append(new Float32Array(160).fill(0.5))
    const blob = capture.finish()
    expect(blob.type).toBe('audio/wav')
    expect(blob.size).toBe(44 + 480 * 2)
    const audio = new DataView(await blob.arrayBuffer())
    expect(String.fromCharCode(...new Uint8Array(audio.buffer, 0, 4))).toBe('RIFF')
    expect(String.fromCharCode(...new Uint8Array(audio.buffer, 8, 4))).toBe('WAVE')
    expect(audio.getUint32(24, true)).toBe(16_000)
    expect(audio.getInt16(44, true)).toBeCloseTo(Math.floor(0.15 * 32767), -1)
    expect(audio.getInt16(44 + 320 * 2, true)).toBeCloseTo(Math.floor(0.5 * 32767), -1)
  })

  it('bounds inactive ring memory and only retains the latest pre-roll frames', async () => {
    const capture = new PcmCaptureBuffer(16_000, 100)
    for (let i = 0; i < 100; i++) capture.append(new Float32Array(160).fill(i / 100))
    capture.start()
    const blob = capture.finish()
    expect(blob.size).toBeLessThanOrEqual(44 + 1760 * 2)
    const view = new DataView(await blob.arrayBuffer())
    expect(view.getInt16(44, true) / 32767).toBeGreaterThan(0.85)
  })

  it('clamps clipping and non-finite samples and discards cancelled segments', async () => {
    const capture = new PcmCaptureBuffer(16_000)
    capture.append(new Float32Array([5, -5, Number.NaN]))
    capture.start()
    capture.append(new Float32Array([Number.POSITIVE_INFINITY]))
    capture.clear()
    capture.append(new Float32Array([0.5]))
    capture.start()
    const view = new DataView(await capture.finish().arrayBuffer())
    expect(view.getUint32(40, true)).toBe(2)
    expect(view.getInt16(44, true)).toBe(16383)
  })

  it('rejects unsupported sample rates', () => {
    expect(() => new PcmCaptureBuffer(0)).toThrow('Unsupported')
    expect(() => new PcmCaptureBuffer(Number.NaN)).toThrow('Unsupported')
  })
})
