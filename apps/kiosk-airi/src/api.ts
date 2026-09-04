import type {
  AgentResponse,
  LanguageCode,
  ProductSearchResponse,
  SessionResponse,
  TranscriptionResponse,
} from './types'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '')
  ?? 'http://127.0.0.1:8000'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
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
    throw new Error(detail)
  }

  return response.json() as Promise<T>
}

async function requestBlob(path: string, init?: RequestInit): Promise<Blob> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      Accept: 'audio/mpeg',
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
    throw new Error(detail)
  }

  return response.blob()
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

export function synthesize(
  sessionId: string,
  text: string,
  language: LanguageCode = 'vi',
): Promise<Blob> {
  return requestBlob('/audio/synthesize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, text, language }),
  })
}

export function searchProducts(query = '', limit = 6): Promise<ProductSearchResponse> {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  return request<ProductSearchResponse>(`/products/search?${params.toString()}`)
}
