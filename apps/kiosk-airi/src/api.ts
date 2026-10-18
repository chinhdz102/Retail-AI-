import type {
  AgentResponse,
  LanguageCode,
  ProductSearchResponse,
  SessionResponse,
  TranscriptionResponse,
} from './types'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '')
  ?? 'http://127.0.0.1:8000'

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 45_000): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...init?.headers,
      },
    })

    if (!response.ok) {
      let detail = `Request failed (${response.status})`
      try {
        const body = await response.json() as { detail?: string }
        detail = body.detail ?? detail
      } catch {
        // Keep the status-based message when the server returns no JSON.
      }
      throw new ApiError(detail, response.status)
    }

    return await response.json() as T
  } catch (error) {
    if (controller.signal.aborted) throw new Error('Kết nối phản hồi quá lâu. Bạn thử lại giúp mình nhé.')
    throw error
  } finally {
    clearTimeout(timer)
  }
}

export function createSession(kioskId = 'kiosk-01'): Promise<SessionResponse> {
  return request<SessionResponse>('/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kiosk_id: kioskId }),
  })
}

export function respond(sessionId: string, text: string, language: LanguageCode = 'vi'): Promise<AgentResponse> {
  return request<AgentResponse>('/agent/respond', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, text, language }),
  })
}

export function transcribe(
  sessionId: string,
  audio: Blob,
  language: LanguageCode = 'vi',
): Promise<TranscriptionResponse> {
  const form = new FormData()
  form.append('session_id', sessionId)
  form.append('language', language)
  form.append('file', audio, 'voice.webm')
  return request<TranscriptionResponse>('/audio/transcribe', {
    method: 'POST',
    body: form,
  })
}

export async function createSynthesisTicket(
  sessionId: string,
  text: string,
  language: LanguageCode = 'vi',
): Promise<string> {
  const result = await request<{ audio_url: string }>('/audio/synthesize/tickets', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, text, language }),
  })
  return result.audio_url.startsWith('http') ? result.audio_url : `${API_BASE_URL}${result.audio_url}`
}

export function searchProducts(query = '', limit = 6): Promise<ProductSearchResponse> {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  return request<ProductSearchResponse>(`/products/search?${params.toString()}`)
}
