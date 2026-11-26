/**
 * Real-time browser-side neural speech denoiser using GTCRN WebAssembly SIMD
 * (`@sapphi-red/gtcrn-wasm`, 196 KB static asset served from `/vad/gtcrn.wasm`).
 *
 * Runs directly on the 16 kHz mono 512-sample frames produced by Silero VAD
 * (`512 = 2 * 256` exact GTCRN frames, zero extra buffering latency, zero server
 * cold-start, zero COEP/SharedArrayBuffer requirement). Suppresses laptop cooling
 * fans, HVAC hum, and supermarket ambient noise before speech reaches Azure STT —
 * even on Vercel serverless deployments!
 */

import type { GtcrnModule, GtcrnProcessor } from '@sapphi-red/gtcrn-wasm'

const GTCRN_FRAME_SIZE = 256

export interface DenoisedFrameMetrics {
  frame: Float32Array
  rawRms: number
  denoisedRms: number
}

export interface BrowserSpeechDenoiser {
  processFrame: (frame: Float32Array) => DenoisedFrameMetrics
  destroy: () => void
}

let modulePromise: Promise<GtcrnModule | null> | null = null
let gtcrnUnavailable = false

function frameRms(samples: Float32Array): number {
  if (!samples.length) return 0
  let sum = 0
  for (let i = 0; i < samples.length; i += 1) {
    const s = samples[i] ?? 0
    sum += s * s
  }
  return Math.sqrt(sum / samples.length)
}

async function loadCachedGtcrnModule(): Promise<GtcrnModule | null> {
  if (import.meta.env.MODE === 'test' || gtcrnUnavailable) return null
  if (typeof WebAssembly === 'undefined' || typeof fetch !== 'function') return null
  if (!modulePromise) {
    modulePromise = (async () => {
      try {
        const [{ loadGtcrnModule }, wasmRes] = await Promise.all([
          import('@sapphi-red/gtcrn-wasm'),
          fetch(`${import.meta.env.BASE_URL}vad/gtcrn.wasm`, { cache: 'force-cache' }),
        ])
        if (!wasmRes.ok) {
          gtcrnUnavailable = true
          return null
        }
        const wasmBinary = await wasmRes.arrayBuffer()
        if (!wasmBinary.byteLength) {
          gtcrnUnavailable = true
          return null
        }
        return await loadGtcrnModule({ wasmBinary })
      } catch {
        gtcrnUnavailable = true
        return null
      }
    })()
  }
  return modulePromise
}

/** Pre-fetch and compile the 196 KB GTCRN WASM binary in the background. */
export async function warmBrowserGtcrnDenoiser(): Promise<boolean> {
  const mod = await loadCachedGtcrnModule()
  return mod !== null
}

/**
 * Create a stateful per-capture-session GTCRN denoiser instance.
 * Returns `null` cleanly when WASM SIMD is unsupported or in unit test mode.
 */
export async function createBrowserGtcrnDenoiser(): Promise<BrowserSpeechDenoiser | null> {
  const mod = await loadCachedGtcrnModule()
  if (!mod) return null
  try {
    const { GtcrnProcessor: ProcessorCtor } = await import('@sapphi-red/gtcrn-wasm')
    return new AdaptiveGtcrnDenoiser(new ProcessorCtor(mod))
  } catch {
    return null
  }
}

export class AdaptiveGtcrnDenoiser implements BrowserSpeechDenoiser {
  private readonly processor: Pick<GtcrnProcessor, 'process' | 'destroy'>
  private destroyed = false
  private remainder = new Float32Array(0)
  private smoothedRawFloor = 0.01
  private smoothedCleanFloor = 0.004

  constructor(processor: Pick<GtcrnProcessor, 'process' | 'destroy'>) {
    this.processor = processor
  }

  processFrame(frame: Float32Array): DenoisedFrameMetrics {
    const rawRms = frameRms(frame)
    if (this.destroyed || !frame.length) {
      return { frame, rawRms, denoisedRms: rawRms }
    }

    // Fast path: exact multiple of 256 samples (Silero VAD v6 always emits 512 = 2 * 256)
    let input = frame
    if (this.remainder.length > 0) {
      const merged = new Float32Array(this.remainder.length + frame.length)
      merged.set(this.remainder, 0)
      merged.set(frame, this.remainder.length)
      input = merged
      this.remainder = new Float32Array(0)
    }

    const fullCount = Math.floor(input.length / GTCRN_FRAME_SIZE) * GTCRN_FRAME_SIZE
    if (fullCount < input.length) {
      this.remainder = input.slice(fullCount)
    }
    if (fullCount === 0) {
      return { frame, rawRms, denoisedRms: rawRms }
    }

    const enhanced = new Float32Array(fullCount)
    try {
      for (let offset = 0; offset < fullCount; offset += GTCRN_FRAME_SIZE) {
        const chunk = input.subarray(offset, offset + GTCRN_FRAME_SIZE)
        const out = this.processor.process(chunk)
        enhanced.set(out, offset)
      }
    } catch {
      return { frame, rawRms, denoisedRms: rawRms }
    }

    const cleanRms = frameRms(enhanced)
    // Track background noise attenuation ratio so heavy fan noise gets aggressive
    // suppression (90% wet) while clean studio-quiet voices keep natural Vietnamese
    // tone harmonics (60% wet, zero metallic artifacts).
    if (cleanRms < 0.025) {
      this.smoothedRawFloor += (Math.min(0.15, rawRms) - this.smoothedRawFloor) * 0.08
      this.smoothedCleanFloor += (Math.min(0.1, cleanRms) - this.smoothedCleanFloor) * 0.08
    }
    const noiseSuppressionRatio = this.smoothedRawFloor / Math.max(0.001, this.smoothedCleanFloor)
    const wet = noiseSuppressionRatio >= 2.2 || this.smoothedRawFloor >= 0.015 ? 0.88 : 0.62
    const dry = 1 - wet

    const blended = new Float32Array(fullCount)
    for (let i = 0; i < fullCount; i += 1) {
      const sample = wet * enhanced[i]! + dry * input[i]!
      blended[i] = sample > 0.99 ? 0.99 : sample < -0.99 ? -0.99 : sample
    }
    return {
      frame: blended,
      rawRms,
      denoisedRms: cleanRms,
    }
  }

  destroy(): void {
    if (this.destroyed) return
    this.destroyed = true
    this.remainder = new Float32Array(0)
    try {
      this.processor.destroy()
    } catch {
      // Ignore cleanup errors on already-disposed WASM instances.
    }
  }
}
