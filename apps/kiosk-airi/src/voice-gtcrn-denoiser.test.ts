import { describe, expect, it, vi } from 'vitest'

import { AdaptiveGtcrnDenoiser } from './voice-gtcrn-denoiser'

describe('AdaptiveGtcrnDenoiser', () => {
  it('processes 512-sample Silero VAD frames as two exact 256-sample GTCRN chunks and suppresses stationary noise', () => {
    const processSpy = vi.fn((chunk: Float32Array) => {
      // Simulate GTCRN attenuating stationary fan noise by 95%
      const out = new Float32Array(chunk.length)
      for (let i = 0; i < chunk.length; i += 1) out[i] = (chunk[i] ?? 0) * 0.05
      return out
    })
    const destroySpy = vi.fn()
    const denoiser = new AdaptiveGtcrnDenoiser({ process: processSpy, destroy: destroySpy })

    const fanFrame = new Float32Array(512).fill(0.06)
    let result = denoiser.processFrame(fanFrame)
    for (let i = 0; i < 8; i += 1) {
      result = denoiser.processFrame(fanFrame)
    }

    expect(processSpy).toHaveBeenCalledTimes(18) // 9 frames * 2 chunks of 256
    expect(result.frame.length).toBe(512)
    expect(result.rawRms).toBeCloseTo(0.06, 3)
    expect(result.denoisedRms).toBeLessThan(0.005)
    // Blended output heavily suppresses constant fan noise (88% wet)
    expect(Math.abs(result.frame[0]!)).toBeLessThan(0.015)

    denoiser.destroy()
    expect(destroySpy).toHaveBeenCalledTimes(1)
    // Post-destroy calls pass through safely without touching freed WASM memory
    const after = denoiser.processFrame(fanFrame)
    expect(after.frame).toBe(fanFrame)
    expect(processSpy).toHaveBeenCalledTimes(18)
  })
})
