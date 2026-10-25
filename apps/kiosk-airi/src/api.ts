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

function createTimedController(timeoutMs: number, parentSignal?: AbortSignal | null) {
  const controller = new AbortController()
  let didTimeout = false
  const abortFromParent = () => controller.abort(parentSignal?.reason)
  if (parentSignal?.aborted) abortFromParent()
  else parentSignal?.addEventListener('abort', abortFromParent, { once: true })
  const timer = setTimeout(() => {
    didTimeout = true
    controller.abort()
  }, timeoutMs)
  return {
    controller,
    didTimeout: () => didTimeout,
    dispose: () => {
      clearTimeout(timer)
      parentSignal?.removeEventListener('abort', abortFromParent)
    },
  }
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 45_000): Promise<T> {
  const { signal: parentSignal, ...requestInit } = init ?? {}
  const timed = createTimedController(timeoutMs, parentSignal)
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...requestInit,
      signal: timed.controller.signal,
      headers: {
        Accept: 'application/json',
        ...requestInit.headers,
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
    if (timed.didTimeout()) throw new Error('Kết nối phản hồi quá lâu. Bạn thử lại giúp mình nhé.')
    throw error
  } finally {
    timed.dispose()
  }
}

async function requestAgentStream(
  sessionId: string,
  text: string,
  language: LanguageCode,
  clientTurnId: string,
  onTextDelta: (text: string) => void,
  signal?: AbortSignal,
  timeoutMs = 45_000,
): Promise<AgentResponse> {
  const timed = createTimedController(timeoutMs, signal)
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  try {
    const response = await fetch(`${API_BASE_URL}/agent/respond/stream`, {
      method: 'POST',
      signal: timed.controller.signal,
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
    const state: {
      metadata: Omit<AgentResponse, 'text'> | null
      sessionValidated: boolean
      completed: boolean
    } = { metadata: null, sessionValidated: false, completed: false }

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
      if (eventName === 'start') {
        if (!payload.response) throw new Error('Invalid response stream metadata.')
        assertResponseSession(sessionId, payload.response)
        state.metadata = payload.response
        state.sessionValidated = true
      } else if (eventName === 'delta') {
        if (typeof payload.text !== 'string') throw new Error('Invalid response stream delta.')
        if (!state.sessionValidated) {
          throw new Error('Response stream sent text before session validation.')
        }
        accumulatedText += payload.text
        onTextDelta(payload.text)
      } else if (eventName === 'complete') {
        if (!payload.response) throw new Error('Invalid final response stream event.')
        if (!state.sessionValidated) {
          throw new Error('Response stream completed before session validation.')
        }
        assertResponseSession(sessionId, payload.response)
        state.metadata = payload.response
        state.completed = true
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
    if (!state.metadata || !state.sessionValidated || !state.completed) {
      throw new Error('The response stream ended before completion.')
    }
    return { ...state.metadata, text: accumulatedText }
  } catch (error) {
    if (timed.didTimeout()) {
      throw new Error('Kết nối phản hồi quá lâu. Bạn thử lại giúp mình nhé.')
    }
    throw error
  } finally {
    if (reader) void reader.cancel().catch(() => undefined)
    timed.dispose()
  }
}

function assertResponseSession(
  expectedSessionId: string,
  response: Pick<AgentResponse, 'session_id' | 'cart' | 'checkout_quote' | 'order' | 'support_ticket'>,
): void {
  const nestedSessionIds = [
    response.cart?.session_id,
    response.checkout_quote?.cart.session_id,
    response.order?.session_id,
    response.support_ticket?.session_id,
  ]
  if (response.session_id !== expectedSessionId
    || nestedSessionIds.some((sessionId) => sessionId !== undefined && sessionId !== expectedSessionId)) {
    throw new Error('Câu trả lời không thuộc phiên hội thoại hiện tại.')
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
  signal?: AbortSignal,
): Promise<AgentResponse> {
  if (onTextDelta) {
    return requestAgentStream(sessionId, text, language, clientTurnId, onTextDelta, signal)
  }
  return request<AgentResponse>('/agent/respond', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, text, language, client_turn_id: clientTurnId }),
    signal,
  }).then((response) => {
    assertResponseSession(sessionId, response)
    return response
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
  signal?: AbortSignal,
): Promise<TranscriptionResponse> {
  const form = new FormData()
  form.append('session_id', sessionId)
  form.append('language', language)
  const audioType = audio.type.split(';')[0].toLowerCase()
  const extension = ({
    'audio/mp4': 'mp4',
    'audio/mpeg': 'mp3',
    'audio/ogg': 'ogg',
    'audio/wav': 'wav',
    'audio/webm': 'webm',
  } as Record<string, string>)[audioType] ?? 'webm'
  form.append('file', audio, `voice.${extension}`)
  return request<TranscriptionResponse>('/audio/transcribe', {
    method: 'POST',
    body: form,
    signal,
  }, 25_000)
}

export function prewarmSpeech(
  sessionId: string,
  language: LanguageCode = 'vi',
): Promise<{ warmed: boolean; provider: string }> {
  return request<{ warmed: boolean; provider: string }>('/audio/synthesize/prewarm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, language }),
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
