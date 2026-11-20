import type {
  AgentResponse,
  CheckoutQuote,
  LanguageCode,
  SandboxOrder,
  ShoppingCart,
  ProductSearchResponse,
  SessionResponse,
  TranscriptionResponse,
  VisionIdentifyResponse,
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
      signal: init?.signal ? AbortSignal.any([controller.signal, init.signal]) : controller.signal,
      headers: {
        Accept: 'application/json',
        ...init?.headers,
      },
    })

    if (!response.ok) {
      let detail = 'Chưa thực hiện được yêu cầu. Bạn thử lại nhé.'
      let errorCode: string | undefined
      try {
        const body = await response.json() as { detail?: string | { code?: string; message?: string } }
        if (typeof body.detail === 'string') detail = body.detail
        else if (body.detail) {
          errorCode = body.detail.code
          detail = body.detail.message ?? detail
        }
      } catch {
        // Keep the status-based message when the server returns no JSON.
      }
      if (path === '/agent/respond' && response.status >= 500) {
        detail = errorCode === 'assistant_busy'
          ? 'Mai đang tạm hết lượt trả lời. Bạn thử lại sau một lát nhé.'
          : response.status === 504
            ? 'Mai đang trả lời lâu hơn dự kiến. Bạn thử lại giúp mình nhé.'
            : 'Mai chưa hoàn tất câu trả lời. Bạn thử lại giúp mình nhé.'
      } else if (response.status === 429) {
        detail = 'Mai đang nhận nhiều yêu cầu. Bạn thử lại sau một lát nhé.'
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
): Promise<AgentResponse> {
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
  signal?: AbortSignal,
): Promise<TranscriptionResponse> {
  const form = new FormData()
  form.append('session_id', sessionId)
  form.append('language', language)
  form.append('file', audio, 'voice.wav')
  return request<TranscriptionResponse>('/audio/transcribe', {
    method: 'POST',
    body: form,
    signal,
  })
}

export function prepareVoice(sessionId: string): Promise<{ ready: boolean }> {
  return request('/audio/prepare', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
  }, 6000)
}

export async function createSynthesisTicket(
  sessionId: string,
  text: string,
  language: LanguageCode = 'vi',
  signal?: AbortSignal,
): Promise<string> {
  const result = await request<{ audio_url: string }>('/audio/synthesize/tickets', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, text, language }),
    signal,
  })
  return result.audio_url.startsWith('http') ? result.audio_url : `${API_BASE_URL}${result.audio_url}`
}

export function searchProducts(query = '', limit = 6): Promise<ProductSearchResponse> {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  return request<ProductSearchResponse>(`/products/search?${params.toString()}`)
}

export function identifyProductImage(
  image: File,
  topK = 5,
): Promise<VisionIdentifyResponse> {
  const form = new FormData()
  form.append('file', image, image.name || 'product-image')
  return request<VisionIdentifyResponse>(`/vision/identify?top_k=${topK}`, {
    method: 'POST',
    body: form,
  })
}
