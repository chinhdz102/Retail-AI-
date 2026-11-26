import type { LanguageCode, TranscriptionResponse } from './types'

const rawApiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim()
const API_BASE_URL = rawApiBase
  ? rawApiBase.replace(/\/$/, '')
  : (import.meta.env.PROD ? '/api' : 'http://127.0.0.1:8000')

export interface StreamingSttOptions {
  sessionId: string
  language: LanguageCode
  onPartial?: (text: string) => void
  WebSocketCtor?: typeof WebSocket
}

export function canUseStreamingStt(serverSupported?: boolean): boolean {
  if (import.meta.env.MODE === 'test') return false
  if (serverSupported === false) return false
  if (typeof WebSocket === 'undefined') return false
  if (
    serverSupported === undefined
    && typeof window !== 'undefined'
    && /\.vercel\.app$/i.test(window.location?.hostname ?? '')
  ) {
    return false
  }
  return true
}

export function buildStreamingSttUrl(sessionId: string, language: LanguageCode): string {
  const resolved = API_BASE_URL.startsWith('/') && typeof window !== 'undefined' && window.location?.origin
    ? `${window.location.origin}${API_BASE_URL}`
    : API_BASE_URL
  const wsBase = resolved.replace(/^http:\/\//i, 'ws://').replace(/^https:\/\//i, 'wss://')
  const params = new URLSearchParams({ session_id: sessionId, language })
  return `${wsBase}/audio/transcribe/stream?${params.toString()}`
}

export function float32ToPcm16Buffer(frame: Float32Array): ArrayBuffer {
  const out = new Int16Array(frame.length)
  for (let i = 0; i < frame.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, frame[i] ?? 0))
    out[i] = sample < 0 ? Math.round(sample * 32768) : Math.round(sample * 32767)
  }
  return out.buffer
}

/**
 * Live WebSocket PCM16 streaming client for Azure Speech STT.
 *
 * Buffers leading pre-roll frames while the socket handshakes, streams every
 * 32 ms Silero VAD frame directly to the backend while the user speaks, emits
 * live partial captions, and resolves cleanly to ``null`` on any error/fallback
 * so the caller transparently falls back to ``POST /audio/transcribe``.
 */
export interface SmartTurnStatus {
  complete: boolean
  probability: number
  holdMs: number
  reason?: string
}

export interface LiveStreamingSttHandle {
  readonly isHealthy: boolean
  readonly latestPartialText: string
  pushFrame: (frame: Float32Array) => void
  checkTurn?: (timeoutMs?: number) => Promise<SmartTurnStatus | null>
  finish: (timeoutMs?: number) => Promise<TranscriptionResponse | null>
  cancel: () => void
}

export class StreamingSttSession implements LiveStreamingSttHandle {
  private ws: WebSocket | null = null
  private readonly pendingFrames: ArrayBuffer[] = []
  private isReady = false
  private closed = false
  private failed = false
  private endRequested = false
  private latestPartial = ''
  private resolveTurnCheck: ((value: SmartTurnStatus | null) => void) | null = null
  private turnCheckTimer: ReturnType<typeof setTimeout> | null = null
  private resolveFinal: ((value: TranscriptionResponse | null) => void) | null = null
  private finalPromise: Promise<TranscriptionResponse | null> | null = null
  private finishTimer: ReturnType<typeof setTimeout> | null = null
  private readonly sessionId: string

  constructor(options: StreamingSttOptions) {
    this.sessionId = options.sessionId
    const Ctor = options.WebSocketCtor ?? (typeof WebSocket !== 'undefined' ? WebSocket : null)
    if (!Ctor) {
      this.failed = true
      this.closed = true
      return
    }
    try {
      const ws = new Ctor(buildStreamingSttUrl(options.sessionId, options.language))
      ws.binaryType = 'arraybuffer'
      this.ws = ws

      ws.onmessage = (event) => {
        if (this.closed || typeof event.data !== 'string') return
        try {
          const msg = JSON.parse(event.data) as {
            type?: string
            text?: string
            session_id?: string
            model?: string
            speech_detected?: boolean
            complete?: boolean
            probability?: number
            hold_ms?: number
            reason?: string
          }
          if (msg.type === 'ready') {
            this.isReady = true
            this.flushPending()
            if (this.endRequested) this.sendEnd()
            return
          }
          if (msg.type === 'partial' && typeof msg.text === 'string' && msg.text.trim()) {
            this.latestPartial = msg.text.trim()
            options.onPartial?.(this.latestPartial)
            return
          }
          if (msg.type === 'turn_status') {
            this.settleTurnCheck({
              complete: msg.complete !== false,
              probability: typeof msg.probability === 'number' ? msg.probability : 0.5,
              holdMs: typeof msg.hold_ms === 'number' ? msg.hold_ms : 0,
              reason: typeof msg.reason === 'string' ? msg.reason : undefined,
            })
            return
          }
          if (msg.type === 'final') {
            const result: TranscriptionResponse = {
              session_id: msg.session_id || this.sessionId,
              language: options.language,
              text: typeof msg.text === 'string' ? msg.text : '',
              model: typeof msg.model === 'string' ? msg.model : 'azure-ws-stream',
              speech_detected: msg.speech_detected !== false && Boolean(msg.text?.trim()),
            }
            this.settle(result)
            return
          }
          if (msg.type === 'fallback' || msg.type === 'error') {
            this.failed = true
            this.settle(null)
          }
        } catch {
          this.failed = true
          this.settle(null)
        }
      }

      ws.onerror = () => {
        this.failed = true
        this.settle(null)
      }

      ws.onclose = () => {
        if (!this.closed) {
          this.failed = true
          this.settle(null)
        }
      }
    } catch {
      this.failed = true
      this.closed = true
    }
  }

  get isHealthy(): boolean {
    return !this.failed && !this.closed
  }

  get latestPartialText(): string {
    return this.latestPartial
  }

  checkTurn(timeoutMs = 240): Promise<SmartTurnStatus | null> {
    if (
      this.closed
      || this.failed
      || this.endRequested
      || !this.isReady
      || !this.ws
      || this.ws.readyState !== WebSocket.OPEN
    ) {
      return Promise.resolve(null)
    }
    this.settleTurnCheck(null)
    return new Promise<SmartTurnStatus | null>((resolve) => {
      this.resolveTurnCheck = resolve
      this.turnCheckTimer = setTimeout(() => {
        this.settleTurnCheck(null)
      }, timeoutMs)
      try {
        this.ws?.send(JSON.stringify({ type: 'check_turn' }))
      } catch {
        this.settleTurnCheck(null)
      }
    })
  }

  pushFrame(frame: Float32Array): void {
    if (this.closed || this.failed || this.endRequested || !frame.length) return
    const pcm = float32ToPcm16Buffer(frame)
    if (this.isReady && this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(pcm)
      } catch {
        this.failed = true
        this.settle(null)
      }
      return
    }
    // Bound pre-open buffer to ~3 seconds (100 frames) so slow handshakes never leak RAM.
    if (this.pendingFrames.length < 100) {
      this.pendingFrames.push(pcm)
    }
  }

  finish(timeoutMs = 3_000): Promise<TranscriptionResponse | null> {
    if (this.finalPromise) return this.finalPromise
    if (this.closed || this.failed || !this.ws) {
      return Promise.resolve(null)
    }
    this.endRequested = true
    this.finalPromise = new Promise<TranscriptionResponse | null>((resolve) => {
      this.resolveFinal = resolve
      this.finishTimer = setTimeout(() => {
        this.failed = true
        this.settle(null)
      }, timeoutMs)
      if (this.isReady && this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.flushPending()
        this.sendEnd()
      }
    })
    return this.finalPromise
  }

  cancel(): void {
    if (this.closed) return
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify({ type: 'cancel' }))
      } catch {
        // Ignore close-time send failures.
      }
    }
    this.settle(null)
  }

  private flushPending(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return
    while (this.pendingFrames.length > 0) {
      const next = this.pendingFrames.shift()
      if (!next) continue
      try {
        this.ws.send(next)
      } catch {
        this.failed = true
        this.settle(null)
        return
      }
    }
  }

  private sendEnd(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return
    try {
      this.ws.send(JSON.stringify({ type: 'end' }))
    } catch {
      this.failed = true
      this.settle(null)
    }
  }

  private settleTurnCheck(result: SmartTurnStatus | null): void {
    if (this.turnCheckTimer) {
      clearTimeout(this.turnCheckTimer)
      this.turnCheckTimer = null
    }
    const resolve = this.resolveTurnCheck
    this.resolveTurnCheck = null
    resolve?.(result)
  }

  private settle(result: TranscriptionResponse | null): void {
    if (this.closed) return
    this.closed = true
    this.settleTurnCheck(null)
    this.pendingFrames.length = 0
    if (this.finishTimer) {
      clearTimeout(this.finishTimer)
      this.finishTimer = null
    }
    const ws = this.ws
    this.ws = null
    if (ws) {
      ws.onmessage = null
      ws.onerror = null
      ws.onclose = null
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        try {
          ws.close()
        } catch {
          // Ignore close errors.
        }
      }
    }
    const resolve = this.resolveFinal
    this.resolveFinal = null
    resolve?.(result)
  }
}
