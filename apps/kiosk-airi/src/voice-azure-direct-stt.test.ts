import { describe, expect, it, vi } from 'vitest'

import { AzureDirectStreamingSttSession } from './voice-azure-direct-stt'

describe('AzureDirectStreamingSttSession', () => {
  it('streams 16kHz PCM frames directly to Azure Speech SDK with PhraseList and returns finalized Vietnamese text', async () => {
    const writtenChunks: ArrayBuffer[] = []
    let pushClosed = false
    const addedPhrases: string[] = []
    let recognizerRef: any = null

    const fakeSdk: any = {
      ResultReason: { RecognizedSpeech: 3, NoMatch: 0 },
      CancellationReason: { Error: 1, EndOfStream: 2 },
      PropertyId: {
        SpeechServiceConnection_InitialSilenceTimeoutMs: 'init',
        SpeechServiceConnection_EndSilenceTimeoutMs: 'end',
        Speech_SegmentationSilenceTimeoutMs: 'seg',
      },
      SpeechConfig: {
        fromAuthorizationToken: vi.fn((_token: string, _region: string) => ({
          speechRecognitionLanguage: '',
          setProperty: vi.fn(),
        })),
      },
      AudioStreamFormat: {
        getWaveFormatPCM: vi.fn(() => ({ sr: 16000, bits: 16, ch: 1 })),
      },
      AudioInputStream: {
        createPushStream: vi.fn(() => ({
          write: (buf: ArrayBuffer) => writtenChunks.push(buf),
          close: () => { pushClosed = true },
        })),
      },
      AudioConfig: {
        fromStreamInput: vi.fn((stream: unknown) => ({ stream })),
      },
      PhraseListGrammar: {
        fromRecognizer: vi.fn(() => ({
          addPhrase: (phrase: string) => addedPhrases.push(phrase),
        })),
      },
      SpeechRecognizer: class FakeSpeechRecognizer {
        recognizing: ((s: unknown, e: any) => void) | null = null
        recognized: ((s: unknown, e: any) => void) | null = null
        canceled: ((s: unknown, e: any) => void) | null = null
        sessionStopped: (() => void) | null = null
        constructor() {
          recognizerRef = this
        }
        startContinuousRecognitionAsync(cb?: () => void) {
          cb?.()
        }
        stopContinuousRecognitionAsync(cb?: () => void) {
          cb?.()
        }
        close() {}
      },
    }

    const partials: string[] = []
    const session = new AzureDirectStreamingSttSession({
      sessionId: 'sess-123',
      language: 'vi',
      onPartial: (text) => partials.push(text),
      loadSdk: async () => fakeSdk,
      fetchToken: async () => ({
        token: 'ephemeral-jwt-token',
        region: 'southeastasia',
        expiresAtMs: Date.now() + 500_000,
        phrasesVi: ['Thanh Lam Mart', 'Vinamilk', 'PayOS'],
      }),
    })

    // Push pre-roll frame before SDK finishes async init
    session.pushFrame(new Float32Array(512).fill(0.1))
    await vi.waitFor(() => expect(recognizerRef).not.toBeNull())
    await vi.waitFor(() => expect(writtenChunks.length).toBe(1))

    expect(addedPhrases).toEqual(['Thanh Lam Mart', 'Vinamilk', 'PayOS'])
    session.pushFrame(new Float32Array(512).fill(0.2))
    expect(writtenChunks.length).toBe(2)

    recognizerRef.recognizing?.(recognizerRef, { result: { text: 'cho mình hỏi' } })
    expect(partials).toEqual(['cho mình hỏi'])
    expect(session.latestPartialText).toBe('cho mình hỏi')

    const finalPromise = session.finish(2_000)
    expect(pushClosed).toBe(true)

    recognizerRef.recognized?.(recognizerRef, {
      result: { reason: fakeSdk.ResultReason.RecognizedSpeech, text: 'Cho mình hỏi sữa Vinamilk' },
    })
    recognizerRef.sessionStopped?.()

    const result = await finalPromise
    expect(result).toEqual({
      session_id: 'sess-123',
      language: 'vi',
      text: 'Cho mình hỏi sữa Vinamilk',
      model: 'azure-browser-ws+gtcrn',
      speech_detected: true,
    })
  })

  it('resolves to null cleanly when STS token is unavailable so caller falls back to REST STT', async () => {
    const session = new AzureDirectStreamingSttSession({
      sessionId: 'sess-123',
      language: 'vi',
      loadSdk: async () => null,
      fetchToken: async () => null,
    })
    const result = await session.finish(500)
    expect(result).toBeNull()
    expect(session.isHealthy).toBe(false)
  })
})
