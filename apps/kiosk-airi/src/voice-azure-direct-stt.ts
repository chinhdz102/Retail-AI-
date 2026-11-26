/**
 * Direct Browser -> Azure AI Speech WebSocket Streaming STT via ephemeral 10-minute
 * STS tokens issued by `POST /api/audio/azure-speech-token`.
 *
 * Works identically on Vercel Serverless, Docker, and local dev — bypassing Vercel's
 * lack of backend WebSocket support while cutting latency by an entire network hop
 * (Browser -> Azure WSS directly) and feeding GTCRN-denoised 16 kHz PCM frames!
 */

import { getAzureSpeechToken } from './api'
import type { LanguageCode, TranscriptionResponse } from './types'
import { float32ToPcm16Buffer, type LiveStreamingSttHandle } from './voice-stream-stt'

type SpeechSdkModule = typeof import('microsoft-cognitiveservices-speech-sdk')

const LOCALE_BY_LANGUAGE: Record<LanguageCode, string> = {
  vi: 'vi-VN',
  en: 'en-US',
  zh: 'zh-CN',
}

export interface CachedAzureToken {
  token: string
  region: string
  expiresAtMs: number
  phrasesVi: string[]
}

let cachedToken: CachedAzureToken | null = null
let tokenFetchPromise: Promise<CachedAzureToken | null> | null = null
let sdkModulePromise: Promise<SpeechSdkModule | null> | null = null

export function clearAzureDirectTokenCache(): void {
  cachedToken = null
  tokenFetchPromise = null
}

export async function loadAzureSpeechSdk(): Promise<SpeechSdkModule | null> {
  if (import.meta.env.MODE === 'test') return null
  if (!sdkModulePromise) {
    sdkModulePromise = import('microsoft-cognitiveservices-speech-sdk').catch(() => {
      sdkModulePromise = null
      return null
    })
  }
  return sdkModulePromise
}

export async function ensureAzureSpeechToken(sessionId: string): Promise<CachedAzureToken | null> {
  if (import.meta.env.MODE === 'test') return null
  const now = Date.now()
  if (cachedToken && cachedToken.expiresAtMs - now > 90_000) {
    return cachedToken
  }
  if (!tokenFetchPromise) {
    tokenFetchPromise = getAzureSpeechToken(sessionId)
      .then((res) => {
        if (!res?.enabled || !res.token || !res.region) return null
        const ttlMs = Math.max(60, res.expires_in_seconds || 480) * 1000
        const next: CachedAzureToken = {
          token: res.token,
          region: res.region,
          expiresAtMs: Date.now() + ttlMs,
          phrasesVi: Array.isArray(res.phrases_vi) ? res.phrases_vi : [],
        }
        cachedToken = next
        return next
      })
      .catch(() => null)
      .finally(() => {
        tokenFetchPromise = null
      })
  }
  return tokenFetchPromise
}

/**
 * Pre-load the Azure Speech JS SDK chunk and fetch a 10-minute ephemeral STS token
 * in the background when voice mode starts so the very first spoken word streams
 * with zero startup delay.
 */
export async function warmAzureDirectStt(sessionId: string): Promise<boolean> {
  if (import.meta.env.MODE === 'test') return false
  const [sdk, tok] = await Promise.all([loadAzureSpeechSdk(), ensureAzureSpeechToken(sessionId)])
  return Boolean(sdk && tok)
}

export interface AzureDirectSttOptions {
  sessionId: string
  language: LanguageCode
  onPartial?: (text: string) => void
  /** Test/custom dependency injection hooks. */
  loadSdk?: () => Promise<SpeechSdkModule | null>
  fetchToken?: (sessionId: string) => Promise<CachedAzureToken | null>
}

export class AzureDirectStreamingSttSession implements LiveStreamingSttHandle {
  private readonly sessionId: string
  private readonly language: LanguageCode
  private readonly onPartial?: (text: string) => void
  private readonly pendingFrames: ArrayBuffer[] = []
  private pushStream: { write: (buf: ArrayBuffer) => void; close: () => void } | null = null
  private recognizer: {
    startContinuousRecognitionAsync: (cb?: () => void, errCb?: (err: string) => void) => void
    stopContinuousRecognitionAsync: (cb?: () => void, errCb?: (err: string) => void) => void
    close: () => void
  } | null = null
  private isReady = false
  private closed = false
  private failed = false
  private endRequested = false
  private latestPartial = ''
  private readonly recognizedSegments: string[] = []
  private resolveFinal: ((value: TranscriptionResponse | null) => void) | null = null
  private finalPromise: Promise<TranscriptionResponse | null> | null = null
  private finishTimer: ReturnType<typeof setTimeout> | null = null
  private eofGraceTimer: ReturnType<typeof setTimeout> | null = null

  constructor(options: AzureDirectSttOptions) {
    this.sessionId = options.sessionId
    this.language = options.language
    this.onPartial = options.onPartial
    void this.initialize(options)
  }

  get isHealthy(): boolean {
    return !this.failed && !this.closed
  }

  get latestPartialText(): string {
    return this.latestPartial
  }

  pushFrame(frame: Float32Array): void {
    if (this.closed || this.failed || this.endRequested || !frame.length) return
    const pcm = float32ToPcm16Buffer(frame)
    if (this.isReady && this.pushStream) {
      try {
        this.pushStream.write(pcm)
      } catch {
        this.failAndSettle()
      }
      return
    }
    if (this.pendingFrames.length < 120) {
      this.pendingFrames.push(pcm)
    }
  }

  finish(timeoutMs = 2_800): Promise<TranscriptionResponse | null> {
    if (this.finalPromise) return this.finalPromise
    if (this.closed || this.failed) {
      return Promise.resolve(null)
    }
    this.endRequested = true
    this.finalPromise = new Promise<TranscriptionResponse | null>((resolve) => {
      this.resolveFinal = resolve
      this.finishTimer = setTimeout(() => {
        const best = this.combinedTranscript()
        if (best) {
          this.settle(this.buildResult(best))
        } else {
          this.failAndSettle()
        }
      }, timeoutMs)
      if (this.isReady && this.pushStream) {
        this.flushPendingAndClosePush()
      }
    })
    return this.finalPromise
  }

  cancel(): void {
    if (this.closed) return
    this.settle(null)
  }

  private async initialize(options: AzureDirectSttOptions): Promise<void> {
    try {
      const sdkLoader = options.loadSdk ?? loadAzureSpeechSdk
      const tokenLoader = options.fetchToken ?? ensureAzureSpeechToken
      const [sdk, tok] = await Promise.all([sdkLoader(), tokenLoader(options.sessionId)])
      if (this.closed || this.failed) return
      if (!sdk || !tok) {
        this.failAndSettle()
        return
      }

      const speechConfig = sdk.SpeechConfig.fromAuthorizationToken(tok.token, tok.region)
      speechConfig.speechRecognitionLanguage = LOCALE_BY_LANGUAGE[options.language] ?? 'vi-VN'
      speechConfig.setProperty(
        sdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs,
        '5000',
      )
      speechConfig.setProperty(
        sdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs,
        '1200',
      )
      speechConfig.setProperty(
        sdk.PropertyId.Speech_SegmentationSilenceTimeoutMs,
        '1200',
      )

      const format = sdk.AudioStreamFormat.getWaveFormatPCM(16_000, 16, 1)
      const pushStream = sdk.AudioInputStream.createPushStream(format)
      const audioConfig = sdk.AudioConfig.fromStreamInput(pushStream)
      const recognizer = new sdk.SpeechRecognizer(speechConfig, audioConfig)

      if (options.language === 'vi' && tok.phrasesVi.length > 0) {
        try {
          const grammar = sdk.PhraseListGrammar.fromRecognizer(recognizer)
          for (const phrase of tok.phrasesVi.slice(0, 48)) {
            grammar.addPhrase(phrase)
          }
        } catch {
          // Phrase hints are best-effort.
        }
      }

      recognizer.recognizing = (_sender, event) => {
        if (this.closed) return
        const piece = event?.result?.text?.trim() ?? ''
        if (!piece) return
        const combined = [...this.recognizedSegments, piece].join(' ').trim()
        this.latestPartial = combined
        this.onPartial?.(combined)
      }

      recognizer.recognized = (_sender, event) => {
        if (this.closed) return
        const reason = event?.result?.reason
        const piece = event?.result?.text?.trim() ?? ''
        if (reason === sdk.ResultReason.RecognizedSpeech && piece) {
          this.recognizedSegments.push(piece)
          const combined = this.recognizedSegments.join(' ').trim()
          this.latestPartial = combined
          this.onPartial?.(combined)
          if (this.endRequested) {
            // Give Azure a brief 180ms window for any trailing segment after EOF, then settle immediately.
            this.scheduleEofGraceSettle(180)
          }
        } else if (this.endRequested && reason === sdk.ResultReason.NoMatch) {
          this.scheduleEofGraceSettle(80)
        }
      }

      recognizer.canceled = (_sender, event) => {
        if (this.closed) return
        if (event?.reason === sdk.CancellationReason.Error) {
          if (/401|403|unauthorized|token|expired/i.test(event.errorDetails || '')) {
            clearAzureDirectTokenCache()
          }
          const best = this.endRequested ? this.combinedTranscript() : ''
          if (best) {
            this.settle(this.buildResult(best))
          } else {
            this.failAndSettle()
          }
          return
        }
        // EndOfStream cancellation after pushStream.close() is the normal completion signal!
        if (this.endRequested) {
          const best = this.combinedTranscript()
          this.settle(best ? this.buildResult(best) : null)
        }
      }

      recognizer.sessionStopped = () => {
        if (this.closed) return
        if (this.endRequested) {
          const best = this.combinedTranscript()
          this.settle(best ? this.buildResult(best) : null)
        }
      }

      this.pushStream = pushStream
      this.recognizer = recognizer

      await new Promise<void>((resolve, reject) => {
        recognizer.startContinuousRecognitionAsync(
          () => resolve(),
          (err) => reject(new Error(err || 'Azure continuous recognition failed')),
        )
      })
      if (this.closed || this.failed) {
        this.disposeResources()
        return
      }
      this.isReady = true
      if (this.endRequested) {
        this.flushPendingAndClosePush()
      } else {
        this.flushPending()
      }
    } catch {
      this.failAndSettle()
    }
  }

  private flushPending(): void {
    if (!this.pushStream) return
    while (this.pendingFrames.length > 0) {
      const next = this.pendingFrames.shift()
      if (!next) continue
      try {
        this.pushStream.write(next)
      } catch {
        this.failAndSettle()
        return
      }
    }
  }

  private flushPendingAndClosePush(): void {
    this.flushPending()
    if (this.closed || this.failed || !this.pushStream) return
    try {
      this.pushStream.close()
    } catch {
      this.failAndSettle()
    }
  }

  private scheduleEofGraceSettle(delayMs: number): void {
    if (this.eofGraceTimer) clearTimeout(this.eofGraceTimer)
    this.eofGraceTimer = setTimeout(() => {
      this.eofGraceTimer = null
      if (this.closed) return
      const best = this.combinedTranscript()
      this.settle(best ? this.buildResult(best) : null)
    }, delayMs)
  }

  private combinedTranscript(): string {
    const finalized = this.recognizedSegments.join(' ').trim()
    return finalized || this.latestPartial.trim()
  }

  private buildResult(text: string): TranscriptionResponse {
    return {
      session_id: this.sessionId,
      language: this.language,
      text,
      model: 'azure-browser-ws+gtcrn',
      speech_detected: Boolean(text.trim()),
    }
  }

  private failAndSettle(): void {
    this.failed = true
    this.settle(null)
  }

  private disposeResources(): void {
    const push = this.pushStream
    this.pushStream = null
    if (push) {
      try { push.close() } catch { /* ignore */ }
    }
    const rec = this.recognizer
    this.recognizer = null
    if (rec) {
      try {
        rec.stopContinuousRecognitionAsync(
          () => { try { rec.close() } catch { /* ignore */ } },
          () => { try { rec.close() } catch { /* ignore */ } },
        )
      } catch {
        try { rec.close() } catch { /* ignore */ }
      }
    }
  }

  private settle(result: TranscriptionResponse | null): void {
    if (this.closed) return
    this.closed = true
    this.pendingFrames.length = 0
    if (this.finishTimer) {
      clearTimeout(this.finishTimer)
      this.finishTimer = null
    }
    if (this.eofGraceTimer) {
      clearTimeout(this.eofGraceTimer)
      this.eofGraceTimer = null
    }
    this.disposeResources()
    const resolve = this.resolveFinal
    this.resolveFinal = null
    resolve?.(result)
  }
}
