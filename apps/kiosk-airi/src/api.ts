import type {
  AgentResponse,
  AssistedOrderCreatePayload,
  AssistedOrderResponse,
  AzureSpeechTokenResponse,
  CatalogSortMode,
  CheckoutQuote,
  LanguageCode,
  ManagerNotifierStatus,
  PayOSStatus,
  ProductCatalogBrowseResponse,
  ProductCatalogIndexResponse,
  SandboxOrder,
  ShoppingCart,
  ProductSearchResponse,
  SessionResponse,
  TranscriptionResponse,
} from './types'

const rawApiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim()
const API_BASE_URL = rawApiBase
  ? rawApiBase.replace(/\/$/, '')
  : (import.meta.env.PROD ? '/api' : 'http://127.0.0.1:8000')

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

const sessionTokens = new Map<string, string>()
let activeSessionToken: string | null = null

function sessionTokenHeader(sessionId?: string): Record<string, string> {
  const token = (sessionId ? sessionTokens.get(sessionId) : null) ?? activeSessionToken
  return token ? { 'X-Kiosk-Session-Token': token } : {}
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
        ...sessionTokenHeader(),
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

    const refreshedToken = response.headers.get('X-Kiosk-Session-Token')
    if (refreshedToken) {
      activeSessionToken = refreshedToken
    }
    const payload = await response.json() as T
    if (refreshedToken && payload && typeof payload === 'object' && 'session_id' in payload) {
      const sid = String((payload as Record<string, unknown>).session_id ?? '')
      if (sid) sessionTokens.set(sid, refreshedToken)
    }
    return payload
  } catch (error) {
    if (timed.didTimeout()) throw new Error('Kết nối phản hồi quá lâu. Bạn thử lại giúp mình nhé.')
    throw error
  } finally {
    timed.dispose()
  }
}

export type TextDeltaHandler = ((text: string) => void) & {
  onStart?: (metadata: Omit<AgentResponse, 'text'>) => void
}

async function requestAgentStream(
  sessionId: string,
  text: string,
  language: LanguageCode,
  clientTurnId: string,
  onTextDelta: TextDeltaHandler,
  signal?: AbortSignal,
  timeoutMs = 45_000,
  onStart?: (metadata: Omit<AgentResponse, 'text'>) => void,
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
        ...sessionTokenHeader(sessionId),
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
        ;(onStart ?? onTextDelta.onStart)?.(payload.response)
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

export async function createSession(kioskId = 'kiosk-01'): Promise<SessionResponse> {
  const session = await request<SessionResponse>('/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kiosk_id: kioskId }),
  })
  if (session.session_token) {
    sessionTokens.set(session.session_id, session.session_token)
    activeSessionToken = session.session_token
  }
  return session
}

export async function closeSession(sessionId: string): Promise<SessionResponse> {
  const closedToken = sessionTokens.get(sessionId)
  try {
    return await request<SessionResponse>(`/sessions/${encodeURIComponent(sessionId)}/close`, {
      method: 'POST',
    })
  } finally {
    sessionTokens.delete(sessionId)
    if (closedToken && activeSessionToken === closedToken) {
      activeSessionToken = null
    }
  }
}

export function respond(
  sessionId: string,
  text: string,
  language: LanguageCode = 'vi',
  clientTurnId = createRequestId(),
  onTextDelta?: TextDeltaHandler,
  signal?: AbortSignal,
  onStart?: (metadata: Omit<AgentResponse, 'text'>) => void,
): Promise<AgentResponse> {
  if (onTextDelta) {
    return requestAgentStream(sessionId, text, language, clientTurnId, onTextDelta, signal, 45_000, onStart)
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
  includeDiagnostics = false,
  clientDenoised = false,
): Promise<TranscriptionResponse> {
  const form = new FormData()
  form.append('session_id', sessionId)
  form.append('language', language)
  if (includeDiagnostics) form.append('include_diagnostics', 'true')
  if (clientDenoised) form.append('client_denoised', 'true')
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

export function getAzureSpeechToken(
  sessionId: string,
): Promise<AzureSpeechTokenResponse> {
  return request<AzureSpeechTokenResponse>(
    '/audio/azure-speech-token',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    },
    8_000,
  )
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

export function respondWithImage(
  sessionId: string,
  file: Blob,
  message = '',
  language: LanguageCode = 'vi',
  signal?: AbortSignal,
): Promise<AgentResponse> {
  const form = new FormData()
  form.append('session_id', sessionId)
  form.append('message', message)
  form.append('language', language)
  const fileName = file instanceof File && file.name
    ? file.name
    : (file.type === 'image/png'
      ? 'product.png'
      : file.type === 'image/webp'
        ? 'product.webp'
        : file.type === 'video/mp4'
          ? 'product.mp4'
          : file.type === 'video/webm'
            ? 'product.webm'
            : file.type === 'video/quicktime'
              ? 'product.mov'
              : 'product.jpg')
  form.append('file', file, fileName)
  return request<AgentResponse>('/agent/respond/image', {
    method: 'POST',
    body: form,
    signal,
  }, 45_000)
}

export function searchProducts(query = '', limit = 6): Promise<ProductSearchResponse> {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  return request<ProductSearchResponse>(`/products/search?${params.toString()}`)
}

export function browseProductCatalog(options: {
  department?: string
  category?: string
  q?: string
  sort?: CatalogSortMode
  offset?: number
  limit?: number
  signal?: AbortSignal
} = {}): Promise<ProductCatalogBrowseResponse> {
  const params = new URLSearchParams()
  if (options.department) params.set('department', options.department)
  if (options.category) params.set('category', options.category)
  if (options.q) params.set('q', options.q)
  if (options.sort) params.set('sort', options.sort)
  if (typeof options.offset === 'number') params.set('offset', String(options.offset))
  if (typeof options.limit === 'number') params.set('limit', String(options.limit))
  const qs = params.toString()
  return request<ProductCatalogBrowseResponse>(
    `/products/catalog${qs ? `?${qs}` : ''}`,
    { signal: options.signal },
    15_000,
  )
}

let catalogIndexPromise: Promise<ProductCatalogIndexResponse> | null = null

async function fetchStaticCatalogIndex(): Promise<ProductCatalogIndexResponse | null> {
  if (typeof fetch !== 'function') return null
  try {
    const res = await fetch('/catalog-index.json', { cache: 'default' })
    if (!res.ok) return null
    const ct = res.headers.get('content-type') ?? ''
    if (!ct.includes('json')) return null
    const data = (await res.json()) as ProductCatalogIndexResponse
    if (data && Array.isArray(data.rows) && data.rows.length > 0) {
      return data
    }
    return null
  } catch {
    return null
  }
}

export function fetchProductCatalogIndex(): Promise<ProductCatalogIndexResponse> {
  if (!catalogIndexPromise) {
    catalogIndexPromise = (async () => {
      const staticIndex = await fetchStaticCatalogIndex()
      if (staticIndex) return staticIndex
      return request<ProductCatalogIndexResponse>(
        '/products/catalog/index',
        undefined,
        15_000,
      )
    })().catch((err) => {
      catalogIndexPromise = null
      throw err
    })
  }
  return catalogIndexPromise
}

export function getManagerNotifierStatus(): Promise<ManagerNotifierStatus> {
  return request<ManagerNotifierStatus>('/support/manager-notifier/status', undefined, 6_000)
}

export function submitAssistedOrder(
  sessionId: string,
  payload: AssistedOrderCreatePayload,
): Promise<AssistedOrderResponse> {
  return request<AssistedOrderResponse>(
    `/sessions/${encodeURIComponent(sessionId)}/assisted-orders`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...payload,
        idempotency_key: payload.idempotency_key || createRequestId(),
      }),
    },
    20_000,
  )
}

export function listAssistedOrders(sessionId: string): Promise<AssistedOrderResponse[]> {
  return request<AssistedOrderResponse[]>(
    `/sessions/${encodeURIComponent(sessionId)}/assisted-orders`,
    undefined,
    10_000,
  )
}

export function getPayOSStatus(): Promise<PayOSStatus> {
  return request<PayOSStatus>('/payments/payos/status', undefined, 6_000)
}

export function syncPayOSPayment(
  payosOrderCode: number,
  paymentVerifyToken?: string | null,
): Promise<AssistedOrderResponse> {
  return request<AssistedOrderResponse>(
    `/payments/payos/${encodeURIComponent(String(payosOrderCode))}/sync`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        payment_verify_token: paymentVerifyToken || undefined,
      }),
    },
    15_000,
  )
}

export function cancelPayOSPayment(
  payosOrderCode: number,
  reason = 'Khách hủy thanh toán tại kiosk',
): Promise<AssistedOrderResponse> {
  return request<AssistedOrderResponse>(
    `/payments/payos/${encodeURIComponent(String(payosOrderCode))}/cancel`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    },
    12_000,
  )
}
