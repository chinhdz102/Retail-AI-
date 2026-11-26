import { describe, expect, it, vi } from 'vitest'

import {
  computeFormantBands,
  computeObjectFitCoverTransform,
  computeTimeDomainRms,
  ensureAudioLipSyncTap,
  LipSyncAnalyzerState,
  sampleTrackAnchor,
  VISEME_INDEX,
  type LipSyncTrackData,
} from './lip-sync'

function makeTrack(): LipSyncTrackData {
  return {
    version: 1,
    videoWidth: 1280,
    videoHeight: 720,
    fps: 24,
    frameCount: 4,
    patchWidth: 112,
    patchHeight: 68,
    visemes: ['closed', 'slight', 'round_small', 'round_open', 'medium', 'wide'],
    anchors: [
      [584, 303],
      [586, 305],
      [588, 303],
      [586, 301],
    ],
  }
}

describe('lip-sync geometry and tracking', () => {
  it('computes exact object-fit: cover transforms for wide and portrait viewports', () => {
    // Wide container: scales by width (1920/1280 = 1.5), centers vertically
    const wide = computeObjectFitCoverTransform(1920, 900, 1280, 720)
    expect(wide.scale).toBeCloseTo(1.5, 5)
    expect(wide.offsetX).toBeCloseTo(0, 5)
    expect(wide.offsetY).toBeCloseTo((900 - 1080) / 2, 5)

    // Portrait container: scales by height (1080/720 = 1.5), centers horizontally
    const portrait = computeObjectFitCoverTransform(600, 1080, 1280, 720)
    expect(portrait.scale).toBeCloseTo(1.5, 5)
    expect(portrait.offsetY).toBeCloseTo(0, 5)
    expect(portrait.offsetX).toBeCloseTo((600 - 1920) / 2, 5)
  })

  it('interpolates sub-pixel anchors smoothly and wraps seamlessly at loop boundary', () => {
    const track = makeTrack()
    // Halfway between frame 0 [584, 303] and frame 1 [586, 305]
    const [midX, midY] = sampleTrackAnchor(track, 0.5 / 24)
    expect(midX).toBeCloseTo(585, 4)
    expect(midY).toBeCloseTo(304, 4)

    // Halfway between last frame 3 [586, 301] and frame 0 [584, 303]
    const [wrapX, wrapY] = sampleTrackAnchor(track, 3.5 / 24)
    expect(wrapX).toBeCloseTo(585, 4)
    expect(wrapY).toBeCloseTo(302, 4)
  })
})

describe('lip-sync acoustic DSP and viseme classification', () => {
  it('computes RMS energy and 3-band Vietnamese formant shares accurately', () => {
    const silence = new Float32Array(256)
    expect(computeTimeDomainRms(silence)).toBe(0)

    const tone = new Float32Array(256).fill(0.2)
    expect(computeTimeDomainRms(tone)).toBeCloseTo(0.2, 5)

    // 48 kHz / 512 FFT -> 93.75 Hz/bin. Low 180-650Hz ~ bins 1..7, mid 650-2100Hz ~ bins 6..23
    const lowBins = new Uint8Array(256)
    for (let i = 2; i <= 6; i += 1) lowBins[i] = 220
    const lowBands = computeFormantBands(lowBins, 48000, 512)
    expect(lowBands.lowShare).toBeGreaterThan(0.6)

    const midBins = new Uint8Array(256)
    for (let i = 8; i <= 20; i += 1) midBins[i] = 220
    const midBands = computeFormantBands(midBins, 48000, 512)
    expect(midBands.midShare).toBeGreaterThan(0.6)
  })

  it('opens wide on loud mid-band vowels, rounds on low-band vowels, and closes on silence pauses', () => {
    const analyzer = new LipSyncAnalyzerState()

    // 1. Silence stays strictly closed
    const silent = analyzer.stepAudio(0.002, { lowShare: 0.33, midShare: 0.5, highShare: 0.17 }, 16.67)
    expect(silent.visemeIndex).toBe(VISEME_INDEX.closed)
    expect(silent.openness).toBe(0)

    // 2. Loud mid-band vowel ("a / ă / e") opens quickly into wide/medium
    let frame = silent
    for (let i = 0; i < 6; i += 1) {
      frame = analyzer.stepAudio(0.18, { lowShare: 0.2, midShare: 0.68, highShare: 0.12 }, 16.67)
    }
    expect([VISEME_INDEX.wide, VISEME_INDEX.medium]).toContain(frame.visemeIndex)
    expect(frame.openness).toBeGreaterThan(0.45)

    // 3. Low-band rounded vowel ("ô / o / u") transitions to rounded viseme
    for (let i = 0; i < 10; i += 1) {
      frame = analyzer.stepAudio(0.14, { lowShare: 0.68, midShare: 0.24, highShare: 0.08 }, 16.67)
    }
    expect([VISEME_INDEX.round_open, VISEME_INDEX.round_small]).toContain(frame.visemeIndex)

    // 4. Pause between clauses (> 45ms silence) closes Mai's mouth cleanly back to 0
    for (let i = 0; i < 16; i += 1) {
      frame = analyzer.stepAudio(0.001, { lowShare: 0.33, midShare: 0.5, highShare: 0.17 }, 16.67)
    }
    expect(frame.visemeIndex).toBe(VISEME_INDEX.closed)
    expect(frame.openness).toBe(0)
  })

  it('classifies Vietnamese word hints accurately for browser speechSynthesis fallback', () => {
    const analyzer = new LipSyncAnalyzerState()
    analyzer.pulseWordBoundary('bốn')
    const roundFrame = analyzer.stepBrowserFallback(true, 40)
    expect([VISEME_INDEX.round_open, VISEME_INDEX.round_small]).toContain(roundFrame.visemeIndex)
    expect(roundFrame.openness).toBeGreaterThan(0.1)

    analyzer.pulseWordBoundary('dạ')
    const wideFrame = analyzer.stepBrowserFallback(true, 40)
    expect([VISEME_INDEX.wide, VISEME_INDEX.medium]).toContain(wideFrame.visemeIndex)

    let closedFrame = wideFrame
    for (let i = 0; i < 18; i += 1) {
      closedFrame = analyzer.stepBrowserFallback(false, 20)
    }
    expect(closedFrame.openness).toBe(0)
    expect(closedFrame.visemeIndex).toBe(VISEME_INDEX.closed)
  })

  it('reuses a single MediaElementAudioSourceNode per HTMLAudioElement via WeakMap', () => {
    const createSourceSpy = vi.fn().mockReturnValue({ connect: vi.fn() })
    const analyserMock = {
      fftSize: 512,
      frequencyBinCount: 256,
      smoothingTimeConstant: 0.25,
      connect: vi.fn(),
      getFloatTimeDomainData: vi.fn(),
      getByteFrequencyData: vi.fn(),
    }
    const createAnalyserSpy = vi.fn().mockReturnValue(analyserMock)
    const resumeSpy = vi.fn().mockResolvedValue(undefined)
    class FakeAudioContext {
      state = 'suspended'
      sampleRate = 48000
      destination = {}
      createMediaElementSource = createSourceSpy
      createAnalyser = createAnalyserSpy
      resume = resumeSpy
    }
    vi.stubGlobal('AudioContext', FakeAudioContext)

    const audio = new Audio()
    const first = ensureAudioLipSyncTap(audio)
    const second = ensureAudioLipSyncTap(audio)
    expect(first).not.toBeNull()
    expect(second).toBe(first)
    expect(createSourceSpy).toHaveBeenCalledTimes(1)
    vi.unstubAllGlobals()
  })

  it('uses passive captureStream without connecting to AudioContext.destination when supported (Chromium #687574 AEC fix)', () => {
    const fakeStream = { getAudioTracks: () => [{}] } as unknown as MediaStream
    const streamSourceConnect = vi.fn()
    const createMediaStreamSourceSpy = vi.fn().mockReturnValue({ connect: streamSourceConnect })
    const createMediaElementSourceSpy = vi.fn()
    const analyserConnect = vi.fn()
    const analyserMock = {
      fftSize: 512,
      frequencyBinCount: 256,
      smoothingTimeConstant: 0.25,
      connect: analyserConnect,
      getFloatTimeDomainData: vi.fn(),
      getByteFrequencyData: vi.fn(),
    }
    class FakeAudioContext {
      state = 'running'
      sampleRate = 48000
      destination = {}
      createMediaStreamSource = createMediaStreamSourceSpy
      createMediaElementSource = createMediaElementSourceSpy
      createAnalyser = vi.fn().mockReturnValue(analyserMock)
      resume = vi.fn().mockResolvedValue(undefined)
    }
    vi.stubGlobal('AudioContext', FakeAudioContext)

    const audio = new Audio() as HTMLAudioElement & { captureStream?: () => MediaStream }
    audio.captureStream = vi.fn().mockReturnValue(fakeStream)

    const tap = ensureAudioLipSyncTap(audio)
    expect(tap).not.toBeNull()
    expect(tap?.mode).toBe('capture-stream')
    expect(createMediaStreamSourceSpy).toHaveBeenCalledWith(fakeStream)
    expect(streamSourceConnect).toHaveBeenCalledWith(analyserMock)
    // CRITICAL: Must NEVER touch createMediaElementSource or connect analyser to destination
    // so Chrome WebRTC AEC3 continues cancelling Mai's TTS voice from the microphone!
    expect(createMediaElementSourceSpy).not.toHaveBeenCalled()
    expect(analyserConnect).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })
})
