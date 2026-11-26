import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  buildRetailAzureSsml,
  clearDirectAzureTtsCache,
  synthesizeDirectAzureAudioBlob,
} from './voice-azure-direct-tts'

describe('voice-azure-direct-tts', () => {
  beforeEach(() => {
    clearDirectAzureTtsCache()
  })

  it('builds XML-safe Vietnamese retail SSML with natural currency and unit pronunciation', () => {
    const ssml = buildRetailAzureSsml('Sữa Vinamilk 1kg giá 44.700đ, giảm 10% <script>', 'vi')
    expect(ssml).toContain('vi-VN-HoaiMyNeural')
    expect(ssml).toContain('alias="một ki lô gam"')
    expect(ssml).toContain('alias="bốn mươi tư nghìn bảy trăm đồng"')
    expect(ssml).toContain('alias="mười phần trăm"')
    expect(ssml).toContain('&lt;script&gt;')
    expect(ssml).not.toContain('<script>')
  })

  it('synthesizes MP3 directly via Azure SpeechSynthesizer with null audioConfig and serves repeat calls from browser LRU cache', async () => {
    const speakSpy = vi.fn((_ssml: string, cb: (r: any) => void) => {
      cb({
        reason: 10,
        audioData: new Uint8Array([0xff, 0xfb, 0x90, 0x64]).buffer,
      })
    })
    const openConnSpy = vi.fn()
    const audioConfigsSeen: unknown[] = []
    const fakeSdk: any = {
      ResultReason: { SynthesizingAudioCompleted: 10 },
      SpeechSynthesisOutputFormat: { Audio24Khz48KBitRateMonoMp3: 6 },
      SpeechConfig: {
        fromAuthorizationToken: vi.fn(() => ({ speechSynthesisOutputFormat: 0 })),
      },
      Connection: {
        fromSynthesizer: vi.fn(() => ({ openConnection: openConnSpy })),
      },
      SpeechSynthesizer: class FakeSpeechSynthesizer {
        constructor(_cfg: unknown, audioConfig: unknown) {
          audioConfigsSeen.push(audioConfig)
        }
        speakSsmlAsync = speakSpy
        close = vi.fn()
      },
    }

    const fetchToken = vi.fn(async () => ({
      token: 'ephemeral-jwt',
      region: 'japaneast',
      expiresAtMs: Date.now() + 500_000,
      phrasesVi: [],
    }))

    const blob1 = await synthesizeDirectAzureAudioBlob('sess-1', 'Xin chào bạn!', 'vi', {
      loadSdk: async () => fakeSdk,
      fetchToken,
      bypassTestGuard: true,
    })
    expect(blob1).not.toBeNull()
    expect(blob1?.type).toBe('audio/mpeg')
    expect(blob1?.size).toBe(4)
    expect(audioConfigsSeen).toEqual([null])
    expect(openConnSpy).toHaveBeenCalledTimes(1)
    expect(speakSpy).toHaveBeenCalledTimes(1)

    // Repeat call hits client-side LRU cache with 0 Azure calls!
    const blob2 = await synthesizeDirectAzureAudioBlob('sess-1', 'Xin chào bạn!', 'vi', {
      loadSdk: async () => fakeSdk,
      fetchToken,
      bypassTestGuard: true,
    })
    expect(blob2?.size).toBe(4)
    expect(speakSpy).toHaveBeenCalledTimes(1)
  })
})
