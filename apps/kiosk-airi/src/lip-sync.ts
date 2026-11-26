export type VisemeName =
  | 'closed'
  | 'slight'
  | 'round_small'
  | 'round_open'
  | 'medium'
  | 'wide'

export const VISEME_INDEX: Record<VisemeName, number> = {
  closed: 0,
  slight: 1,
  round_small: 2,
  round_open: 3,
  medium: 4,
  wide: 5,
}

export interface LipSyncTrackData {
  version: number
  videoWidth: number
  videoHeight: number
  fps: number
  frameCount: number
  patchWidth: number
  patchHeight: number
  visemes: VisemeName[]
  anchors: Array<[number, number]>
}

export interface LipSyncFrame {
  /** Primary viseme sprite index (0..5). */
  visemeIndex: number
  /** Secondary viseme sprite index for smooth cross-alpha transitions. */
  secondaryIndex: number
  /** Overall alpha of the primary mouth sprite over the closed-mouth base (0..1). */
  openness: number
  /** Alpha of the secondary mouth sprite for smooth viseme blending (0..1). */
  secondaryWeight: number
  /** Raw instantaneous speech RMS energy (0..1). */
  energy: number
  /** True when live audio-driven lip-sync is actively driving the avatar. */
  active: boolean
}

export interface CoverViewportTransform {
  scale: number
  offsetX: number
  offsetY: number
  drawWidth: number
  drawHeight: number
}

export const IDLE_LIP_SYNC_FRAME: Readonly<LipSyncFrame> = Object.freeze({
  visemeIndex: VISEME_INDEX.closed,
  secondaryIndex: VISEME_INDEX.closed,
  openness: 0,
  secondaryWeight: 0,
  energy: 0,
  active: false,
})

const SILENCE_RMS_THRESHOLD = 0.014
const PAUSE_CLOSE_MS = 30
const ATTACK_TAU_MS = 24
const RELEASE_TAU_MS = 40
const FORMANT_TAU_MS = 55

function clamp(value: number, min: number, max: number): number {
  return value < min ? min : value > max ? max : value
}

function expAlpha(dtMs: number, tauMs: number): number {
  if (dtMs <= 0 || tauMs <= 0) return 1
  return 1 - Math.exp(-dtMs / tauMs)
}

/**
 * Computes the exact CSS `object-fit: cover; object-position: center` transform
 * from source video pixels (videoWidth x videoHeight) into a rendered canvas/box.
 */
export function computeObjectFitCoverTransform(
  containerWidth: number,
  containerHeight: number,
  videoWidth: number,
  videoHeight: number,
): CoverViewportTransform {
  if (containerWidth <= 0 || containerHeight <= 0 || videoWidth <= 0 || videoHeight <= 0) {
    return { scale: 1, offsetX: 0, offsetY: 0, drawWidth: 0, drawHeight: 0 }
  }
  const scale = Math.max(containerWidth / videoWidth, containerHeight / videoHeight)
  const drawWidth = videoWidth * scale
  const drawHeight = videoHeight * scale
  const offsetX = (containerWidth - drawWidth) / 2
  const offsetY = (containerHeight - drawHeight) / 2
  return { scale, offsetX, offsetY, drawWidth, drawHeight }
}

/**
 * Maps a playback timestamp (in seconds) to a smoothly interpolated sub-pixel
 * mouth anchor inside the precomputed face-tracking loop.
 */
export function sampleTrackAnchor(
  track: LipSyncTrackData,
  currentTimeSec: number,
): [number, number] {
  const count = track.anchors.length
  if (count === 0) return [0, 0]
  if (count === 1) return track.anchors[0]
  const fps = track.fps > 0 ? track.fps : 24
  const safeTime = Number.isFinite(currentTimeSec) && currentTimeSec >= 0 ? currentTimeSec : 0
  const rawFrame = (safeTime * fps) % count
  const i0 = Math.floor(rawFrame) % count
  const i1 = (i0 + 1) % count
  const frac = rawFrame - Math.floor(rawFrame)
  const [x0, y0] = track.anchors[i0]
  const [x1, y1] = track.anchors[i1]
  return [x0 + (x1 - x0) * frac, y0 + (y1 - y0) * frac]
}

export interface SpectralBands {
  lowShare: number
  midShare: number
  highShare: number
}

/**
 * Extracts normalized 3-band Vietnamese vowel formant energy shares from FFT bins:
 *  - low  (180-650 Hz):  rounded back vowels (u, ô, o, ư)
 *  - mid  (650-2100 Hz): open & mid vowels (a, ă, â, e, ê, ơ)
 *  - high (2100-5500 Hz): sibilants & front high vowels (i, s, x, ch, tr)
 */
export function computeFormantBands(
  frequencyBins: Uint8Array,
  sampleRate: number,
  fftSize: number,
): SpectralBands {
  if (!frequencyBins.length || sampleRate <= 0 || fftSize <= 0) {
    return { lowShare: 0.33, midShare: 0.5, highShare: 0.17 }
  }
  const binHz = sampleRate / fftSize
  const maxBin = frequencyBins.length - 1
  const meanBand = (minHz: number, maxHz: number): number => {
    const start = clamp(Math.floor(minHz / binHz), 0, maxBin)
    const end = clamp(Math.ceil(maxHz / binHz), start, maxBin)
    let sum = 0
    for (let i = start; i <= end; i += 1) {
      sum += frequencyBins[i] ?? 0
    }
    return sum / Math.max(1, end - start + 1)
  }

  const low = meanBand(180, 650)
  const mid = meanBand(650, 2100)
  const high = meanBand(2100, 5500)
  const total = low + mid + high
  if (total < 6) {
    return { lowShare: 0.33, midShare: 0.5, highShare: 0.17 }
  }
  return {
    lowShare: low / total,
    midShare: mid / total,
    highShare: high / total,
  }
}

export function computeTimeDomainRms(samples: Float32Array): number {
  if (!samples.length) return 0
  let sumSq = 0
  for (let i = 0; i < samples.length; i += 1) {
    const s = samples[i] ?? 0
    sumSq += s * s
  }
  return Math.sqrt(sumSq / samples.length)
}

/**
 * Pure stateful acoustic-to-viseme mapper.
 * Converts 60fps time-domain RMS + 3-band FFT formants into smooth, syllable-articulated
 * Vietnamese viseme blend weights that close cleanly during every speech pause.
 */
export class LipSyncAnalyzerState {
  private peakRms = 0.13
  private fastLevel = 0
  private slowLevel = 0
  private smoothOpenness = 0
  private silenceMs = 1000
  private lowShare = 0.33
  private midShare = 0.5
  private highShare = 0.17
  private syllableCount = 0
  private inSyllablePeak = false
  private prevViseme: number = VISEME_INDEX.closed
  private visemeBlend = 1
  private browserPulseMs = 0
  private browserPulsePeak = 0
  private browserPulseViseme: number = VISEME_INDEX.medium
  private browserPhaseMs = 0

  reset(): void {
    this.peakRms = 0.13
    this.fastLevel = 0
    this.slowLevel = 0
    this.smoothOpenness = 0
    this.silenceMs = 1000
    this.lowShare = 0.33
    this.midShare = 0.5
    this.highShare = 0.17
    this.syllableCount = 0
    this.inSyllablePeak = false
    this.prevViseme = VISEME_INDEX.closed
    this.visemeBlend = 1
    this.browserPulseMs = 0
    this.browserPulsePeak = 0
    this.browserPhaseMs = 0
  }

  /**
   * Injects a word/syllable boundary pulse for browser `speechSynthesis` fallback,
   * where Web Audio `AnalyserNode` cannot tap system TTS output directly.
   */
  pulseWordBoundary(wordHint = ''): void {
    // Strip Vietnamese tone marks while preserving vowel base letters (ố->o, ủ->u, ạ->a, ệ->e, ị->i).
    const normalized = wordHint
      .trim()
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
    const hasRoundVowel = /[ou]/.test(normalized)
    const hasWideVowel = /[ae]/.test(normalized)
    const hasNarrowVowel = /[iy]/.test(normalized)

    this.syllableCount += 1
    this.browserPulseMs = 210
    this.browserPulsePeak = hasWideVowel ? 0.92 : hasRoundVowel ? 0.8 : 0.72
    if (hasRoundVowel && !hasWideVowel) {
      this.browserPulseViseme = this.syllableCount % 2 === 0 ? VISEME_INDEX.round_open : VISEME_INDEX.round_small
    } else if (hasWideVowel) {
      this.browserPulseViseme = this.syllableCount % 3 === 0 ? VISEME_INDEX.wide : VISEME_INDEX.medium
    } else if (hasNarrowVowel) {
      this.browserPulseViseme = VISEME_INDEX.slight
    } else {
      const cycle = [VISEME_INDEX.medium, VISEME_INDEX.wide, VISEME_INDEX.round_open, VISEME_INDEX.slight]
      this.browserPulseViseme = cycle[this.syllableCount % cycle.length]
    }
  }

  /**
   * Advances state from live Web Audio samples (`rms` + spectral formant shares).
   */
  stepAudio(rms: number, bands: SpectralBands, dtMs: number): LipSyncFrame {
    const safeDt = clamp(dtMs, 4, 120)
    const safeRms = Number.isFinite(rms) && rms > 0 ? rms : 0

    // Adaptive AGC peak tracker so both soft and loud TTS passages articulate naturally
    if (safeRms > this.peakRms) {
      this.peakRms += (safeRms - this.peakRms) * expAlpha(safeDt, 150)
    } else if (safeRms > SILENCE_RMS_THRESHOLD) {
      this.peakRms += (Math.max(safeRms, 0.09) - this.peakRms) * expAlpha(safeDt, 1400)
    }
    this.peakRms = clamp(this.peakRms, 0.07, 0.36)

    const rawLevel = safeRms <= SILENCE_RMS_THRESHOLD
      ? 0
      : clamp((safeRms - SILENCE_RMS_THRESHOLD) / Math.max(0.03, this.peakRms - SILENCE_RMS_THRESHOLD), 0, 1)

    if (rawLevel <= 0.05) {
      this.silenceMs = Math.min(2000, this.silenceMs + safeDt)
    } else {
      this.silenceMs = 0
    }

    // Track fast vs slow envelope for crisp Vietnamese syllable onset & inter-syllable dip
    this.fastLevel += (rawLevel - this.fastLevel) * expAlpha(safeDt, 18)
    this.slowLevel += (rawLevel - this.slowLevel) * expAlpha(safeDt, 110)
    const delta = this.fastLevel - this.slowLevel

    if (delta > 0.05 && !this.inSyllablePeak) {
      this.inSyllablePeak = true
      this.syllableCount += 1
    } else if (delta < -0.025) {
      this.inSyllablePeak = false
    }

    // Smooth formant shares only when speech is present so pauses don't bias vowel shape
    if (rawLevel > 0.05) {
      const fAlpha = expAlpha(safeDt, FORMANT_TAU_MS)
      this.lowShare += (bands.lowShare - this.lowShare) * fAlpha
      this.midShare += (bands.midShare - this.midShare) * fAlpha
      this.highShare += (bands.highShare - this.highShare) * fAlpha
    }

    let targetOpenness = 0
    if (this.silenceMs < PAUSE_CLOSE_MS && rawLevel > 0.04) {
      // Nonlinear compression gives clear opening on medium syllables + dynamic syllable emphasis
      const compressed = Math.pow(rawLevel, 0.78)
      const syllableAccent = clamp(delta * 1.55, -0.28, 0.24)
      targetOpenness = clamp(compressed + syllableAccent, 0.08, 1)
    }

    const tau = targetOpenness >= this.smoothOpenness ? ATTACK_TAU_MS : RELEASE_TAU_MS
    this.smoothOpenness += (targetOpenness - this.smoothOpenness) * expAlpha(safeDt, tau)
    if (this.silenceMs >= PAUSE_CLOSE_MS && this.smoothOpenness < 0.055) {
      this.smoothOpenness = 0
    }

    return this.buildFrame(this.smoothOpenness, safeRms, safeDt, true)
  }

  /**
   * Advances state for browser `speechSynthesis` fallback using `onboundary` pulses
   * (or a soft periodic cadence if the browser does not emit `onboundary` events).
   */
  stepBrowserFallback(speaking: boolean, dtMs: number): LipSyncFrame {
    const safeDt = clamp(dtMs, 4, 120)
    if (!speaking) {
      this.browserPulseMs = 0
      this.smoothOpenness += (0 - this.smoothOpenness) * expAlpha(safeDt, RELEASE_TAU_MS)
      if (this.smoothOpenness < 0.03) this.smoothOpenness = 0
      return {
        visemeIndex: this.smoothOpenness > 0.03 ? this.browserPulseViseme : VISEME_INDEX.closed,
        secondaryIndex: VISEME_INDEX.closed,
        openness: this.smoothOpenness,
        secondaryWeight: 0,
        energy: 0,
        active: this.smoothOpenness > 0.03,
      }
    }

    this.browserPhaseMs += safeDt
    let target = 0
    if (this.browserPulseMs > 0) {
      this.browserPulseMs = Math.max(0, this.browserPulseMs - safeDt)
      const progress = 1 - this.browserPulseMs / 210
      target = Math.sin(Math.PI * clamp(progress, 0, 1)) * this.browserPulsePeak
    } else {
      // Gentle rhythmic fallback (~3.8 syllables/s with short phrase pauses) if onboundary didn't fire
      const phraseCycle = (this.browserPhaseMs % 1850) / 1850
      if (phraseCycle < 0.85) {
        const syl = 0.5 * (1 - Math.cos((this.browserPhaseMs / 250) * Math.PI * 2))
        target = 0.18 + 0.62 * syl
        const cycleIdx = Math.floor(this.browserPhaseMs / 250) % 4
        this.browserPulseViseme = [
          VISEME_INDEX.medium,
          VISEME_INDEX.wide,
          VISEME_INDEX.round_open,
          VISEME_INDEX.slight,
        ][cycleIdx]
      }
    }

    const tau = target >= this.smoothOpenness ? ATTACK_TAU_MS : RELEASE_TAU_MS
    this.smoothOpenness += (target - this.smoothOpenness) * expAlpha(safeDt, tau)
    const openness = clamp(this.smoothOpenness, 0, 1)
    return {
      visemeIndex: openness < 0.08 ? VISEME_INDEX.closed : this.browserPulseViseme,
      secondaryIndex: VISEME_INDEX.closed,
      openness,
      secondaryWeight: 0,
      energy: openness * 0.15,
      active: true,
    }
  }

  private classifyViseme(openness: number): number {
    if (openness < 0.11) return VISEME_INDEX.closed

    // Calibrated empirically against Web Audio AnalyserNode dB-scaled Vietnamese female
    // speech (vi-VN-HoaiMyNeural), compensating for natural -6 dB/octave glottal tilt:
    //   - Rounded back vowels (o, ô, u): low-mid gap > 0.27 with subdued high energy
    //   - Bright front / sibilant (i, s, x, ch, tr): highShare > 0.235
    //   - Open / mid vowels (a, ă, â, e, ê, ơ): medium or wide by loudness
    const lowMidDiff = this.lowShare - this.midShare
    const isRoundDominant = lowMidDiff > 0.27 && this.highShare < 0.20
    const isHighSibilant = this.highShare > 0.235 && lowMidDiff < 0.22

    if (openness < 0.34) {
      return isRoundDominant ? VISEME_INDEX.round_small : VISEME_INDEX.slight
    }
    if (isRoundDominant) {
      return openness >= 0.56 ? VISEME_INDEX.round_open : VISEME_INDEX.round_small
    }
    if (isHighSibilant && openness < 0.62) {
      return VISEME_INDEX.slight
    }
    if (openness >= 0.62) {
      return VISEME_INDEX.wide
    }
    return VISEME_INDEX.medium
  }

  private buildFrame(openness: number, energy: number, dtMs: number, active: boolean): LipSyncFrame {
    const nextViseme = this.classifyViseme(openness)
    let secondaryIndex = this.prevViseme
    let secondaryWeight = 0

    if (nextViseme !== this.prevViseme) {
      // Short 45ms cross-alpha dissolve between distinct viseme shapes to avoid visual snapping
      this.visemeBlend = clamp(this.visemeBlend - dtMs / 45, 0, 1)
      if (this.visemeBlend <= 0.02) {
        this.prevViseme = nextViseme
        this.visemeBlend = 1
        secondaryIndex = nextViseme
        secondaryWeight = 0
      } else {
        secondaryIndex = this.prevViseme
        secondaryWeight = this.visemeBlend
      }
    } else {
      this.visemeBlend = 1
      secondaryIndex = nextViseme
      secondaryWeight = 0
    }

    // Scale overall visibility alpha smoothly over the closed-mouth base video
    const primaryAlpha = nextViseme === VISEME_INDEX.closed
      ? 0
      : clamp(openness * 1.45, 0.28, 1) * (1 - secondaryWeight * 0.65)
    const secondaryAlpha = secondaryIndex === VISEME_INDEX.closed
      ? 0
      : secondaryWeight * clamp(openness * 1.35, 0.25, 1)

    return {
      visemeIndex: nextViseme,
      secondaryIndex,
      openness: clamp(primaryAlpha, 0, 1),
      secondaryWeight: clamp(secondaryAlpha, 0, 1),
      energy,
      active,
    }
  }
}

interface AudioTapHandle {
  context: AudioContext
  source: AudioNode
  analyser: AnalyserNode
  mode: 'capture-stream' | 'media-element'
  timeBuffer: Float32Array<ArrayBuffer>
  freqBuffer: Uint8Array<ArrayBuffer>
}

// Keep at most one Web Audio tap per HTMLAudioElement to prevent InvalidStateError.
const audioTapCache = new WeakMap<HTMLAudioElement, AudioTapHandle>()

function getAudioContextConstructor(): typeof AudioContext | null {
  if (typeof globalThis === 'undefined') return null
  const host = globalThis as unknown as {
    AudioContext?: typeof AudioContext
    webkitAudioContext?: typeof AudioContext
  }
  return host.AudioContext ?? host.webkitAudioContext ?? null
}

type CapturableAudioElement = HTMLAudioElement & {
  captureStream?: () => MediaStream
  mozCaptureStream?: () => MediaStream
}

/**
 * Safely attaches a Web Audio `AnalyserNode` tap to an `HTMLAudioElement` for real-time
 * 60fps lip-sync analysis WITHOUT triggering Chromium Issue #687574 (where routing
 * `createMediaElementSource()` into `AudioContext.destination` detaches the audio from
 * Chrome's WebRTC AEC3 reference loop and causes microphone self-echo).
 *
 * When `audio.captureStream()` is supported (Chrome/Edge/Firefox), we passively tap a
 * silent copy into `AnalyserNode` and NEVER connect to `context.destination`, keeping
 * native `<audio>` speaker playback 100% visible to browser/OS echo cancellation.
 */
export function ensureAudioLipSyncTap(audio: HTMLAudioElement): AudioTapHandle | null {
  const existing = audioTapCache.get(audio)
  if (existing) {
    if (existing.context.state === 'suspended') {
      void existing.context.resume().catch(() => undefined)
    }
    return existing
  }
  const AudioCtx = getAudioContextConstructor()
  if (!AudioCtx) return null
  const capturable = audio as CapturableAudioElement
  const captureFn = typeof capturable.captureStream === 'function'
    ? capturable.captureStream
    : typeof capturable.mozCaptureStream === 'function'
      ? capturable.mozCaptureStream
      : null

  try {
    const context = new AudioCtx()
    if (typeof context.createAnalyser !== 'function') {
      void context.close?.().catch(() => undefined)
      return null
    }
    const analyser = context.createAnalyser()
    analyser.fftSize = 512
    analyser.smoothingTimeConstant = 0.25

    let source: AudioNode | null = null
    let mode: AudioTapHandle['mode'] = 'media-element'

    if (captureFn && typeof context.createMediaStreamSource === 'function') {
      try {
        const stream = captureFn.call(capturable)
        // Passive read-only tap: do NOT connect analyser to context.destination!
        // <audio> continues playing natively with Chromium/OS AEC intact.
        source = context.createMediaStreamSource(stream)
        source.connect(analyser)
        mode = 'capture-stream'
      } catch {
        // On Chromium where captureStream exists, never fall back to
        // createMediaElementSource -> context.destination (which breaks AEC3).
        void context.close?.().catch(() => undefined)
        return null
      }
    } else if (typeof context.createMediaElementSource === 'function') {
      source = context.createMediaElementSource(audio)
      source.connect(analyser)
      analyser.connect(context.destination)
      mode = 'media-element'
    } else {
      void context.close?.().catch(() => undefined)
      return null
    }

    const handle: AudioTapHandle = {
      context,
      source,
      analyser,
      mode,
      timeBuffer: new Float32Array(new ArrayBuffer(analyser.fftSize * 4)),
      freqBuffer: new Uint8Array(new ArrayBuffer(analyser.frequencyBinCount)),
    }
    audioTapCache.set(audio, handle)
    if (context.state === 'suspended') {
      void context.resume().catch(() => undefined)
    }
    return handle
  } catch {
    return null
  }
}

/**
 * Reads the instantaneous output RMS (0..1) of an active `HTMLAudioElement`
 * through its existing Web Audio `AnalyserNode` tap, or returns `null` when
 * no tap is attached yet (for example in non-WebAudio environments).
 */
export function sampleAudioElementRms(audio: HTMLAudioElement | null): number | null {
  if (!audio || audio.paused || audio.ended) return 0
  const tap = audioTapCache.get(audio)
  if (!tap) return null
  if (tap.context.state === 'suspended') {
    void tap.context.resume().catch(() => undefined)
    return null
  }
  tap.analyser.getFloatTimeDomainData(tap.timeBuffer)
  return computeTimeDomainRms(tap.timeBuffer)
}

/**
 * Reads the live speech RMS & spectral formant bands from an attached `HTMLAudioElement`
 * and steps the `LipSyncAnalyzerState` forward by `dtMs`.
 */
export function sampleAudioElementLipSync(
  audio: HTMLAudioElement,
  analyzer: LipSyncAnalyzerState,
  dtMs: number,
): LipSyncFrame | null {
  const tap = ensureAudioLipSyncTap(audio)
  if (!tap) return null
  if (tap.context.state === 'suspended') {
    void tap.context.resume().catch(() => undefined)
  }
  tap.analyser.getFloatTimeDomainData(tap.timeBuffer)
  tap.analyser.getByteFrequencyData(tap.freqBuffer)
  const rms = computeTimeDomainRms(tap.timeBuffer)
  const bands = computeFormantBands(tap.freqBuffer, tap.context.sampleRate, tap.analyser.fftSize)
  return analyzer.stepAudio(rms, bands, dtMs)
}
