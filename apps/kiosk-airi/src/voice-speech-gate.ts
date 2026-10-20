/**
 * Conservative energy-envelope evidence gate for the existing MediaRecorder path.
 * This is NOT a neural speech VAD. It suppresses stationary noise/tones that can
 * trigger RMS-only capture, without buffering or recording audio itself.
 * Manual push-to-talk always bypasses it.
 */
export class SpeechEnvelopeGate {
  private voicedMs = 0
  private frames = 0
  private min = Infinity
  private max = 0
  private sum = 0
  private variation = 0
  private previous: number | null = null

  reset(): void {
    this.voicedMs = 0
    this.frames = 0
    this.min = Infinity
    this.max = 0
    this.sum = 0
    this.variation = 0
    this.previous = null
  }

  observe(level: number, elapsedMs: number): void {
    if (!Number.isFinite(level) || !Number.isFinite(elapsedMs) || elapsedMs <= 0) return
    this.voicedMs += Math.min(elapsedMs, 80)
    this.frames += 1
    this.min = Math.min(this.min, level)
    this.max = Math.max(this.max, level)
    this.sum += level
    if (this.previous !== null) this.variation += Math.abs(level - this.previous)
    this.previous = level
  }

  /**
   * Only classify a long, nearly flat RMS envelope as stationary interference.
   * This is not proof that the input is non-speech. Never use it to reject
   * variable environmental noise or to replace a speech-trained VAD.
   */
  stationaryNoiseLevel(): number | null {
    if (this.frames < 12 || this.voicedMs < 480) return null
    const mean = this.sum / this.frames
    if (mean <= 0) return null
    const flatRange = this.max - this.min <= Math.max(0.002, mean * 0.06)
    const flatChanges = this.variation <= Math.max(0.003, mean * 0.08) * (this.frames - 1)
    return flatRange && flatChanges ? mean : null
  }

  hasSpeechEvidence(): boolean {
    if (this.frames < 4 || this.voicedMs < 160) return false
    const mean = this.sum / this.frames
    // Noise of a nearly constant amplitude is not enough evidence of speech.
    // A lower absolute floor preserves quiet voices while relative thresholds
    // adapt to louder microphones/automatic gain control.
    const minRange = Math.max(0.0015, mean * 0.12)
    const minVariation = Math.max(0.003, mean * 0.30)
    return this.max - this.min >= minRange && this.variation >= minVariation
  }
}
