/** PCM ring buffer for optional AudioWorklet capture.
 * Retains up to 350ms before detected speech so initial consonants survive
 * the slower 40ms RMS polling loop. Frames are always cloned (worklet ownership).
 */
export class PcmCaptureBuffer {
  private ring: Float32Array[] = []
  private ringSamples = 0
  private recorded: Float32Array[] | null = null
  private readonly preRollSamples: number

  constructor(readonly sampleRate: number, preRollMs = 350) {
    if (!Number.isFinite(sampleRate) || sampleRate < 8000 || sampleRate > 192000) {
      throw new Error('Unsupported audio sample rate')
    }
    this.preRollSamples = Math.round(sampleRate * preRollMs / 1000)
  }

  append(frame: Float32Array): void {
    if (!frame.length) return
    const copy = new Float32Array(frame)
    if (this.recorded) {
      this.recorded.push(copy)
      return
    }
    this.ring.push(copy)
    this.ringSamples += copy.length
    while (this.ringSamples > this.preRollSamples && this.ring.length > 1) {
      const removed = this.ring.shift()!
      this.ringSamples -= removed.length
    }
  }

  start(): void {
    if (this.recorded) throw new Error('PCM capture already started')
    this.recorded = [...this.ring]
    this.ring = []
    this.ringSamples = 0
  }

  finish(): Blob {
    const frames = this.recorded ?? []
    this.recorded = null
    const samples = frames.reduce((sum, f) => sum + f.length, 0)
    const audio = new ArrayBuffer(44 + samples * 2)
    const view = new DataView(audio)
    const ascii = (offset: number, value: string) => {
      for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i))
    }
    ascii(0, 'RIFF')
    view.setUint32(4, audio.byteLength - 8, true)
    ascii(8, 'WAVE')
    ascii(12, 'fmt ')
    view.setUint32(16, 16, true)
    view.setUint16(20, 1, true)
    view.setUint16(22, 1, true)
    view.setUint32(24, this.sampleRate, true)
    view.setUint32(28, this.sampleRate * 2, true)
    view.setUint16(32, 2, true)
    view.setUint16(34, 16, true)
    ascii(36, 'data')
    view.setUint32(40, samples * 2, true)
    let offset = 44
    for (const frame of frames) {
      for (const original of frame) {
        const value = Math.max(-1, Math.min(1, Number.isFinite(original) ? original : 0))
        view.setInt16(offset, value < 0 ? value * 32768 : value * 32767, true)
        offset += 2
      }
    }
    return new Blob([audio], { type: 'audio/wav' })
  }

  clear(): void {
    this.ring = []
    this.ringSamples = 0
    this.recorded = null
  }
}
