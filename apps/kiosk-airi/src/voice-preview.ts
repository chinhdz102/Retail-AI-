/** Overlap ASR with endpoint waiting, without ending or trimming the real turn. */
export class SpeechPreview {
  private quietFrames = 0
  private attempts = 0
  private current: number | undefined
  private static sequence = 0
  private readonly callbacks: {
    ready: (audio: Float32Array, id: number) => void
    invalidated: (id: number) => void
  }

  constructor(callbacks: {
    ready: (audio: Float32Array, id: number) => void
    invalidated: (id: number) => void
  }) { this.callbacks = callbacks }

  frame(probability: number, frames: readonly { frame: Float32Array }[], confirmed: boolean) {
    // Require confident silence, more conservative than the endpoint's .35.
    // Even uncertain/quiet speech invalidates the speculative result.
    if (probability >= .1) {
      this.quietFrames = 0
      this.invalidate()
      return
    }
    if (!confirmed || !frames.length) return
    if (++this.quietFrames < 16 || this.current !== undefined || this.attempts >= 2) return
    const audio = new Float32Array(frames.reduce((size, item) => size + item.frame.length, 0))
    let offset = 0
    for (const item of frames) { audio.set(item.frame, offset); offset += item.frame.length }
    this.current = ++SpeechPreview.sequence
    ++this.attempts
    this.callbacks.ready(audio, this.current)
  }

  finish(): number | undefined {
    const id = this.current
    this.current = undefined
    this.quietFrames = 0
    this.attempts = 0
    return id
  }

  reset() {
    this.invalidate()
    this.quietFrames = 0
    this.attempts = 0
  }

  private invalidate() {
    if (this.current !== undefined) this.callbacks.invalidated(this.current)
    this.current = undefined
  }
}

/** A preview has no UI/history side effects. Only the final endpoint can take it. */
export class TranscriptionPreview<T> {
  private pending?: { id: number; session: string; controller: AbortController; result: Promise<T | null> }

  start(id: number, session: string, request: (signal: AbortSignal) => Promise<T>) {
    this.cancel()
    const controller = new AbortController()
    this.pending = {
      id, session, controller,
      result: request(controller.signal).catch(() => null),
    }
  }

  take(id: number | undefined, session: string) {
    if (id === undefined || this.pending?.id !== id || this.pending.session !== session) {
      this.cancel()
      return undefined
    }
    const pending = this.pending
    this.pending = undefined
    return pending
  }

  cancel(id?: number) {
    if (id !== undefined && this.pending?.id !== id) return
    this.pending?.controller.abort()
    this.pending = undefined
  }
}
