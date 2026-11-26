/**
 * Direct Browser -> Azure Neural TTS via `microsoft-cognitiveservices-speech-sdk`
 * WebSocket (`SpeechSynthesizer`) sharing the exact same 10-minute STS token as
 * Direct Azure Streaming STT.
 *
 * Benefits:
 * - ZERO Vercel serverless invocations / streaming bandwidth for TTS audio!
 * - Lower latency (1 hop Browser -> Azure WSS instead of Browser -> Vercel -> Azure).
 * - Returns raw MP3 `Blob` played through the app's AEC-safe `<audio>` element so
 *   Chromium AEC3 lip-sync (`captureStream`) and Speaker Echo Shield stay intact.
 * - Includes an in-memory 32-entry LRU cache so repeated replies play in 0ms.
 */

import type { LanguageCode } from './types'
import {
  clearAzureDirectTokenCache,
  ensureAzureSpeechToken,
  loadAzureSpeechSdk,
  type CachedAzureToken,
} from './voice-azure-direct-stt'

type SpeechSdkModule = typeof import('microsoft-cognitiveservices-speech-sdk')

const VOICES: Record<LanguageCode, { locale: string; voiceId: string }> = {
  vi: { locale: 'vi-VN', voiceId: 'vi-VN-HoaiMyNeural' },
  en: { locale: 'en-US', voiceId: 'en-US-JennyNeural' },
  zh: { locale: 'zh-CN', voiceId: 'zh-CN-XiaoxiaoNeural' },
}

const DIGITS = ['không', 'một', 'hai', 'ba', 'bốn', 'năm', 'sáu', 'bảy', 'tám', 'chín'] as const
const SCALES = ['', 'nghìn', 'triệu', 'tỷ', 'nghìn tỷ'] as const

const UNITS: Record<string, string> = {
  ml: 'mi li lít',
  l: 'lít',
  'lít': 'lít',
  lit: 'lít',
  kg: 'ki lô gam',
  g: 'gam',
  gr: 'gam',
  mg: 'mi li gam',
  kcal: 'ki lô ca lo',
  ppm: 'pê pê em',
}

const STATIC_ALIASES: Record<string, string> = {
  'co₂': 'xê ô hai',
  'p/s': 'pê ét',
  '3in1': 'ba trong một',
  '2in1': 'hai trong một',
  nrc: 'nước rửa chén',
  gvs: 'giấy vệ sinh',
  kdr: 'kem đánh răng',
  nxv: 'nước xả vải',
}

function escapeXml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}

function readThreeDigits(value: number, forceHundreds: boolean): string {
  const hundreds = Math.floor(value / 100)
  const remainder = value % 100
  const words: string[] = []
  if (hundreds > 0 || forceHundreds) words.push(`${DIGITS[hundreds]} trăm`)
  if (!remainder) return words.join(' ')
  const tens = Math.floor(remainder / 10)
  const units = remainder % 10
  if (tens > 0) {
    words.push(tens === 1 ? 'mười' : `${DIGITS[tens]} mươi`)
    if (units > 0) {
      words.push(
        units === 5
          ? 'lăm'
          : units === 1 && tens > 1
            ? 'mốt'
            : units === 4 && tens > 1
              ? 'tư'
              : DIGITS[units]!,
      )
    }
  } else {
    if (hundreds > 0 || forceHundreds) words.push('lẻ')
    words.push(DIGITS[units]!)
  }
  return words.join(' ')
}

function vietnameseInteger(value: number): string {
  if (!Number.isSafeInteger(value) || value < 0) return String(value)
  if (value === 0) return DIGITS[0]
  const groups: number[] = []
  let remaining = value
  while (remaining > 0) {
    groups.push(remaining % 1000)
    remaining = Math.floor(remaining / 1000)
  }
  const words: string[] = []
  for (let i = groups.length - 1; i >= 0; i -= 1) {
    const group = groups[i]!
    if (!group) continue
    words.push(readThreeDigits(group, i < groups.length - 1))
    if (i > 0 && SCALES[i]) words.push(SCALES[i]!)
  }
  return words.join(' ')
}

function spokenNumber(raw: string, allowDecimal: boolean): string | null {
  const digits = raw.replace(/[.,]/g, '')
  if (!digits || digits.length > 15) return null
  if (raw.length > 1 && raw[0] === '0' && /\d/.test(raw[1] ?? '')) return null
  if (/^(?:\d+|\d{1,3}(?:\.\d{3})+)$/.test(raw)) {
    return vietnameseInteger(Number(raw.replace(/\./g, '')))
  }
  if (allowDecimal && /^\d+[,.]\d{1,6}$/.test(raw)) {
    const [intPart, fracPart] = raw.split(/[,.]/)
    const fracWords = fracPart!.split('').map((d) => DIGITS[Number(d)] ?? d).join(' ')
    return `${vietnameseInteger(Number(intPart))} phẩy ${fracWords}`
  }
  return null
}

/**
 * Mirrors `backend/services/speech_text.py` so browser-direct Azure TTS speaks
 * Vietnamese prices ("44.700đ"), measurements ("500ml", "1kg"), and percentages
 * identically to the backend Azure Speech synthesizer.
 */
export function buildRetailAzureSsml(text: string, language: LanguageCode): string {
  const voice = VOICES[language] ?? VOICES.vi
  const trimmed = text.trim()
  let inner = escapeXml(trimmed)
  if (language === 'vi') {
    const source = trimmed.replace(/\s*;\s*/g, ', ')
    const tokenRe = /(?<![\w.,/:+-])(\d[\d.,]*)\s*(vnd|đ|₫|%|kcal|ppm|kg|gr|mg|g|ml|lít|lit|l)(?!\w)|(?<![\w/-])(CO₂|P\/S|3in1|2in1|NRC|GVS|KDR|NXV)(?![\w/-])/gi
    const parts: string[] = []
    let cursor = 0
    for (const match of source.matchAll(tokenRe)) {
      const idx = match.index ?? 0
      parts.push(escapeXml(source.slice(cursor, idx)))
      const raw = match[0]
      const escapedRaw = escapeXml(raw)
      let alias: string | null = null
      if (match[1] && match[2]) {
        const unitKey = match[2].toLowerCase()
        if (unitKey === '%') {
          const w = spokenNumber(match[1], true)
          if (w) alias = `${w} phần trăm`
        } else if (unitKey === 'đ' || unitKey === '₫' || unitKey === 'vnd') {
          const w = spokenNumber(match[1], false)
          if (w) alias = `${w} đồng`
        } else if (UNITS[unitKey]) {
          const w = spokenNumber(match[1], true)
          if (w) alias = `${w} ${UNITS[unitKey]}`
        }
      } else if (match[3]) {
        const key = match[3].toLowerCase()
        if (['nrc', 'gvs', 'kdr', 'nxv'].includes(key)) {
          alias = match[3] === match[3].toUpperCase() ? (STATIC_ALIASES[key] ?? null) : null
        } else {
          alias = STATIC_ALIASES[key] ?? null
        }
      }
      parts.push(alias ? `<sub alias="${escapeXml(alias)}">${escapedRaw}</sub>` : escapedRaw)
      cursor = idx + raw.length
    }
    parts.push(escapeXml(source.slice(cursor)))
    inner = parts.join('')
  }
  return (
    `<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="${voice.locale}">`
    + `<voice name="${voice.voiceId}">${inner}</voice></speak>`
  )
}

const MAX_CACHE_ENTRIES = 32
const MAX_CACHE_BYTES = 256 * 1024
const audioLruCache = new Map<string, ArrayBuffer>()

function cacheGet(key: string): ArrayBuffer | null {
  const hit = audioLruCache.get(key)
  if (!hit) return null
  audioLruCache.delete(key)
  audioLruCache.set(key, hit)
  return hit
}

function cachePut(key: string, data: ArrayBuffer): void {
  if (data.byteLength === 0 || data.byteLength > MAX_CACHE_BYTES) return
  audioLruCache.delete(key)
  audioLruCache.set(key, data)
  while (audioLruCache.size > MAX_CACHE_ENTRIES) {
    const oldest = audioLruCache.keys().next().value
    if (oldest !== undefined) audioLruCache.delete(oldest)
    else break
  }
}

interface PooledSynthesizer {
  token: string
  region: string
  synth: {
    speakSsmlAsync: (
      ssml: string,
      cb: (result: { reason: number; audioData?: ArrayBuffer; errorDetails?: string }) => void,
      err: (error: string) => void,
    ) => void
    close: () => void
  }
}

let pooledSynth: PooledSynthesizer | null = null

export function closeDirectAzureTts(): void {
  const current = pooledSynth
  pooledSynth = null
  if (current) {
    try { current.synth.close() } catch { /* ignore */ }
  }
}

function getOrCreateSynthesizer(sdk: SpeechSdkModule, tok: CachedAzureToken): PooledSynthesizer {
  if (pooledSynth && pooledSynth.token === tok.token && pooledSynth.region === tok.region) {
    return pooledSynth
  }
  closeDirectAzureTts()
  const speechConfig = sdk.SpeechConfig.fromAuthorizationToken(tok.token, tok.region)
  speechConfig.speechSynthesisOutputFormat = sdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3
  // Pass `null` audioConfig so SDK returns raw MP3 ArrayBuffer instead of seizing
  // speakers directly — letting our AEC-safe <audio> element handle playback + lip-sync.
  const synth = new sdk.SpeechSynthesizer(speechConfig, null as unknown as undefined)
  try {
    sdk.Connection.fromSynthesizer(synth).openConnection()
  } catch {
    // Pre-open is best-effort; speakSsmlAsync opens lazily if needed.
  }
  pooledSynth = { token: tok.token, region: tok.region, synth }
  return pooledSynth
}

export interface DirectAzureTtsDeps {
  loadSdk?: () => Promise<SpeechSdkModule | null>
  fetchToken?: (sessionId: string) => Promise<CachedAzureToken | null>
  bypassTestGuard?: boolean
}

/** Pre-open the reusable Azure TTS WebSocket in the background when voice mode starts. */
export async function warmDirectAzureTts(sessionId: string): Promise<boolean> {
  if (import.meta.env.MODE === 'test') return false
  const [sdk, tok] = await Promise.all([loadAzureSpeechSdk(), ensureAzureSpeechToken(sessionId)])
  if (!sdk || !tok) return false
  try {
    getOrCreateSynthesizer(sdk, tok)
    return true
  } catch {
    return false
  }
}

/**
 * Synthesize spoken MP3 directly from Browser -> Azure Speech WSS using the cached
 * 10-minute STS token (0 Vercel requests, 0 exposed secrets).
 */
export async function synthesizeDirectAzureAudioBlob(
  sessionId: string,
  text: string,
  language: LanguageCode,
  deps?: DirectAzureTtsDeps,
): Promise<Blob | null> {
  if (import.meta.env.MODE === 'test' && !deps?.bypassTestGuard) return null
  const trimmed = text.trim()
  if (!trimmed) return null

  const ssml = buildRetailAzureSsml(trimmed, language)
  const cacheKey = `${language}:${ssml}`
  const cached = cacheGet(cacheKey)
  if (cached) {
    return new Blob([cached], { type: 'audio/mpeg' })
  }

  try {
    const sdkLoader = deps?.loadSdk ?? loadAzureSpeechSdk
    const tokenLoader = deps?.fetchToken ?? ensureAzureSpeechToken
    const [sdk, tok] = await Promise.all([sdkLoader(), tokenLoader(sessionId)])
    if (!sdk || !tok) return null

    const pool = getOrCreateSynthesizer(sdk, tok)
    const audioData = await new Promise<ArrayBuffer | null>((resolve) => {
      let settled = false
      const timer = setTimeout(() => {
        if (settled) return
        settled = true
        closeDirectAzureTts()
        resolve(null)
      }, 10_000)

      const done = (buf: ArrayBuffer | null) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        resolve(buf)
      }

      try {
        pool.synth.speakSsmlAsync(
          ssml,
          (result) => {
            if (
              result?.reason === sdk.ResultReason.SynthesizingAudioCompleted
              && result.audioData
              && result.audioData.byteLength > 0
            ) {
              done(result.audioData)
              return
            }
            if (/401|403|unauthorized|token|expired/i.test(result?.errorDetails || '')) {
              clearAzureDirectTokenCache()
            }
            closeDirectAzureTts()
            done(null)
          },
          (err) => {
            if (/401|403|unauthorized|token|expired/i.test(err || '')) {
              clearAzureDirectTokenCache()
            }
            closeDirectAzureTts()
            done(null)
          },
        )
      } catch {
        closeDirectAzureTts()
        done(null)
      }
    })

    if (!audioData || audioData.byteLength === 0) return null
    cachePut(cacheKey, audioData)
    return new Blob([audioData], { type: 'audio/mpeg' })
  } catch {
    return null
  }
}

export function clearDirectAzureTtsCache(): void {
  audioLruCache.clear()
  closeDirectAzureTts()
}
