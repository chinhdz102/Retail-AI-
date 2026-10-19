import type {
  AgentResponse,
  CheckoutQuote,
  LanguageCode,
  SandboxOrder,
  ShoppingCart,
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

export function createRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
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

async function requestAgentStream(
  sessionId: string,
  text: string,
  language: LanguageCode,
  clientTurnId: string,
  onTextDelta: (text: string) => void,
  timeoutMs = 45_000,
): Promise<AgentResponse> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  try {
    const response = await fetch(`${API_BASE_URL}/agent/respond/stream`, {
      method: 'POST',
      signal: controller.signal,
      headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        session_id: sessionId,
        text,
        language,
        client_turn_id: clientTurnId,
      }),
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
    if (!response.body) throw new Error('The response stream is not available.')

    reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let accumulatedText = ''
    const state: { metadata: Omit<AgentResponse, 'text'> | null } = { metadata: null }

    const consumeFrame = (frame: string) => {
      let eventName = 'message'
      const data: string[] = []
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim()
        else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
      }
      if (!data.length) return
      const payload = JSON.parse(data.join('\n')) as {
        text?: string
        response?: Omit<AgentResponse, 'text'>
      }
      if (eventName === 'delta') {
        if (typeof payload.text !== 'string') throw new Error('Invalid response stream delta.')
        accumulatedText += payload.text
        onTextDelta(payload.text)
      } else if (eventName === 'complete') {
        if (!payload.response) throw new Error('Invalid final response stream event.')
        state.metadata = payload.response
      }
    }

    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      const frames = buffer.split(/\r?\n\r?\n/)
      buffer = frames.pop() ?? ''
      for (const frame of frames) consumeFrame(frame)
      if (done) break
    }
    if (buffer.trim()) consumeFrame(buffer)
    if (!state.metadata) throw new Error('The response stream ended before completion.')
    return { ...state.metadata, text: accumulatedText }
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error('Kết nối phản hồi quá lâu. Bạn thử lại giúp mình nhé.')
    }
    throw error
  } finally {
    if (reader) void reader.cancel().catch(() => undefined)
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

export function closeSession(sessionId: string): Promise<SessionResponse> {
  return request<SessionResponse>(`/sessions/${encodeURIComponent(sessionId)}/close`, {
    method: 'POST',
  })
}

export function respond(
  sessionId: string,
  text: string,
  language: LanguageCode = 'vi',
  clientTurnId = createRequestId(),
  onTextDelta?: (text: string) => void,
): Promise<AgentResponse> {
  if (onTextDelta) {
    return requestAgentStream(sessionId, text, language, clientTurnId, onTextDelta)
  }
  return request<AgentResponse>('/agent/respond', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, text, language, client_turn_id: clientTurnId }),
  })
}

export function getCart(sessionId: string): Promise<ShoppingCart> {
  return request<ShoppingCart>(`/sessions/${encodeURIComponent(sessionId)}/cart`)
}

export function addCartItem(
  sessionId: string,
  productId: string,
  quantity = 1,
  idempotencyKey = createRequestId(),
): Promise<ShoppingCart> {
  return request<ShoppingCart>(`/sessions/${encodeURIComponent(sessionId)}/cart/items`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ product_id: productId, quantity, idempotency_key: idempotencyKey }),
  })
}

export function setCartItemQuantity(
  sessionId: string,
  productId: string,
  quantity: number,
  idempotencyKey = createRequestId(),
): Promise<ShoppingCart> {
  return request<ShoppingCart>(
    `/sessions/${encodeURIComponent(sessionId)}/cart/items/${encodeURIComponent(productId)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ quantity, idempotency_key: idempotencyKey }),
    },
  )
}

export function prepareCheckout(sessionId: string): Promise<CheckoutQuote> {
  return request<CheckoutQuote>(`/sessions/${encodeURIComponent(sessionId)}/checkout/prepare`, {
    method: 'POST',
  })
}

export function cancelCheckout(sessionId: string): Promise<ShoppingCart> {
  return request<ShoppingCart>(`/sessions/${encodeURIComponent(sessionId)}/checkout/cancel`, {
    method: 'POST',
  })
}

export function confirmCheckout(
  sessionId: string,
  confirmationToken: string,
  idempotencyKey = createRequestId(),
): Promise<SandboxOrder> {
  return request<SandboxOrder>(`/sessions/${encodeURIComponent(sessionId)}/checkout/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirmation_token: confirmationToken, idempotency_key: idempotencyKey }),
  })
}

export function simulateSandboxPayment(
  sessionId: string,
  orderId: string,
  outcome: 'PAID' | 'FAILED' | 'CANCELLED',
  eventId = createRequestId(),
): Promise<SandboxOrder> {
  return request<SandboxOrder>(
    `/sessions/${encodeURIComponent(sessionId)}/orders/${encodeURIComponent(orderId)}/sandbox-payment`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ outcome, event_id: eventId }),
    },
  )
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
