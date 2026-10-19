import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App.vue'

const apiMocks = vi.hoisted(() => ({
  createSession: vi.fn(),
  createRequestId: vi.fn(() => 'request-test-id'),
  closeSession: vi.fn().mockResolvedValue({}),
  respond: vi.fn(),
  transcribe: vi.fn(),
  createSynthesisTicket: vi.fn(),
  getCart: vi.fn().mockResolvedValue(null),
  addCartItem: vi.fn(),
  setCartItemQuantity: vi.fn(),
  prepareCheckout: vi.fn(),
  confirmCheckout: vi.fn(),
  cancelCheckout: vi.fn(),
  simulateSandboxPayment: vi.fn(),
}))

vi.mock('./api', () => apiMocks)

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise })
  return { promise, resolve }
}

describe('kiosk app', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.documentElement.lang = 'vi'
    window.localStorage.removeItem('retail-ai-microphone-device')
    apiMocks.createSession.mockResolvedValue({
      session_id: 'session-test',
      kiosk_id: 'kiosk-01',
      state: 'BROWSING',
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    apiMocks.respond.mockResolvedValue({
      session_id: 'session-test',
      text: 'Coca-Cola Zero hiện có giá 13.000đ.',
      model: 'qwen/qwen3.8-27b',
      grounded_product_ids: ['COKE_ZERO_330'],
      state: 'BROWSING',
    })
    apiMocks.transcribe.mockResolvedValue({
      session_id: 'session-test',
      text: 'Cho tôi biết giá Coca Zero.',
      language: 'vi',
      model: 'whisper-large-v3-turbo',
    })
  })

  afterEach(() => {
    delete (window as Window & { SpeechRecognition?: unknown }).SpeechRecognition
    delete (window as Window & { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition
    Reflect.deleteProperty(navigator, 'mediaDevices')
    window.localStorage.removeItem('retail-ai-microphone-device')
  })

  it('keeps the text drawer closed while showing only the avatar controls', async () => {
    const wrapper = mount(App)

    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await vi.waitFor(() => expect(wrapper.get('model-viewer')).toBeTruthy())

    expect(wrapper.find('.top-bar').exists()).toBe(false)
    expect(wrapper.find('.stage-features').exists()).toBe(false)
    expect(wrapper.find('.chat-panel').exists()).toBe(false)
    expect(wrapper.find('.vrm-status').exists()).toBe(false)
    expect(wrapper.get('button[aria-label="Mở phần chữ"]')).toBeTruthy()
  })

  it('opens and closes the text drawer on demand', async () => {
    const wrapper = mount(App)

    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')

    expect(wrapper.get('.text-drawer')).toBeTruthy()
    expect(wrapper.get('.chat-panel')).toBeTruthy()

    await wrapper.get('button[aria-label="Đóng phần chữ"]').trigger('click')

    expect(wrapper.find('.text-drawer').exists()).toBe(false)
  })

  it('closes the backend session when the kiosk component is unloaded', async () => {
    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.getCart).toHaveBeenCalledWith('session-test'))

    wrapper.unmount()

    expect(apiMocks.closeSession).toHaveBeenCalledWith('session-test')
  })

  it('shares the first session boot when a customer sends before startup finishes', async () => {
    const session = deferred<{
      session_id: string
      kiosk_id: string
      state: string
      expires_at: string
    }>()
    apiMocks.createSession.mockReturnValueOnce(session.promise)
    apiMocks.respond.mockResolvedValueOnce({
      session_id: 'session-test', text: 'Mình tìm thấy Coca Zero.', model: 'catalog',
      grounded_product_ids: [], state: 'BROWSING',
    })

    const wrapper = mount(App)
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
    await wrapper.get('button[data-quick-prompt="Giá Coca Zero"]').trigger('click')

    expect(apiMocks.createSession).toHaveBeenCalledTimes(1)
    session.resolve({
      session_id: 'session-test', kiosk_id: 'kiosk-01', state: 'BROWSING',
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    await vi.waitFor(() => expect(apiMocks.respond).toHaveBeenCalledWith(
      'session-test', 'Giá Coca Zero', 'vi', expect.any(String),
    ))
    expect(apiMocks.createSession).toHaveBeenCalledTimes(1)
  })

  it('does not render a late customer response after ending and replacing the session', async () => {
    const oldResponse = deferred<{
      session_id: string
      text: string
      model: string
      grounded_product_ids: string[]
      state: string
    }>()
    const expiresAt = new Date(Date.now() + 30 * 60 * 1000).toISOString()
    apiMocks.createSession.mockResolvedValueOnce({
      session_id: 'customer-a', kiosk_id: 'kiosk-01', state: 'BROWSING', expires_at: expiresAt,
    }).mockResolvedValueOnce({
      session_id: 'customer-b', kiosk_id: 'kiosk-01', state: 'BROWSING', expires_at: expiresAt,
    })
    apiMocks.respond.mockReturnValueOnce(oldResponse.promise)

    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalledTimes(1))
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
    await wrapper.get('button[data-quick-prompt="Giá Coca Zero"]').trigger('click')
    await vi.waitFor(() => expect(apiMocks.respond).toHaveBeenCalledTimes(1))
    expect(wrapper.text()).toContain('Giá Coca Zero')

    await wrapper.get('.end-session-button').trigger('click')
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalledTimes(2))
    expect(wrapper.findAll('.message-row')).toHaveLength(0)

    oldResponse.resolve({
      session_id: 'customer-a',
      text: 'Thông tin riêng của khách A không được hiện cho khách B.',
      model: 'qwen/qwen3.8-27b', grounded_product_ids: [], state: 'BROWSING',
    })
    await flushPromises()

    expect(wrapper.findAll('.message-row')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('Thông tin riêng của khách A')
    expect(apiMocks.respond).toHaveBeenCalledTimes(1)
    expect(apiMocks.createSession).toHaveBeenCalledTimes(2)
  })

  it('switches the chat to English and sends the selected language to the API', async () => {
    const wrapper = mount(App)

    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
    await wrapper.get('button[aria-label="English"]').trigger('click')

    expect(wrapper.text()).toContain('What can I help you find?')
    expect(document.documentElement.lang).toBe('en-US')
    expect(wrapper.get('textarea').attributes('placeholder')).toBe('Try: How much is Coca Zero?')
    await wrapper.get('textarea').setValue('How much is Coca-Cola Zero?')
    await wrapper.get('.composer').trigger('submit')
    await vi.waitFor(() =>
      expect(apiMocks.respond).toHaveBeenCalledWith(
        'session-test',
        'How much is Coca-Cola Zero?',
        'en',
        expect.any(String),
      ),
    )
  })

  it('does not add an unsolicited chat message when the session starts', async () => {
    const wrapper = mount(App)

    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')

    expect(wrapper.findAll('.message-row')).toHaveLength(0)
    expect(wrapper.text()).toContain('Bạn muốn tìm gì hôm nay?')
    expect(apiMocks.respond).not.toHaveBeenCalled()
  })

  it('creates and displays a clearly marked demo support request', async () => {
    apiMocks.respond.mockResolvedValueOnce({
      session_id: 'session-test',
      text: 'Mình đã tạo yêu cầu hỗ trợ demo mã ticket-demo-1. Hệ thống chưa liên hệ nhân viên thật.',
      model: 'support-simulator',
      grounded_product_ids: [],
      tools_used: ['handoff_to_staff'],
      state: 'HANDOFF',
      support_ticket: {
        ticket_id: 'ticket-demo-1',
        session_id: 'session-test',
        kiosk_id: 'kiosk-01',
        issue_type: 'OTHER',
        description: 'Mình muốn gặp nhân viên.',
        status: 'RECEIVED_DEMO',
        created_at: new Date().toISOString(),
        demo: true,
      },
    })
    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')

    await wrapper.get('.support-request-button').trigger('click')

    await vi.waitFor(() => expect(apiMocks.respond).toHaveBeenCalledWith(
      'session-test',
      'Mình muốn gặp nhân viên.',
      'vi',
      expect.any(String),
    ))
    await vi.waitFor(() => expect(wrapper.find('.support-ticket-card').exists()).toBe(true))
    expect(wrapper.get('.support-ticket-card').text()).toContain('ticket-demo-1')
    expect(wrapper.get('.support-ticket-card').text()).toContain('Chỉ là bản demo')
  })

  it('defaults to the physical AMD microphone and hides duplicate browser aliases', async () => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        enumerateDevices: vi.fn(async () => [
          { deviceId: 'default', groupId: 'amd', kind: 'audioinput', label: 'Default - Microphone Array (AMD Audio Device)' },
          { deviceId: 'desk-in', groupId: 'deskin', kind: 'audioinput', label: 'Microphone (DeskIn(R) Virtual Audio Device)' },
          { deviceId: 'amd-direct', groupId: 'amd', kind: 'audioinput', label: 'Microphone Array (AMD Audio Device)' },
          { deviceId: 'communications', groupId: 'amd', kind: 'audioinput', label: 'Communications - Microphone Array (AMD Audio Device)' },
        ]),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      },
    })

    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
    const microphonePicker = wrapper.get('select[aria-label="Chọn micro đầu vào"]')
    await vi.waitFor(() =>
      expect((microphonePicker.element as HTMLSelectElement).value).toBe('amd-direct'),
    )

    const microphoneOptions = wrapper
      .findAll('select[aria-label="Chọn micro đầu vào"] option')
      .map((option) => option.text())
    expect(microphoneOptions).toEqual([
      'Micro mặc định',
      'Microphone (DeskIn(R) Virtual Audio Device)',
      'Microphone Array (AMD Audio Device)',
    ])
    expect(window.localStorage.getItem('retail-ai-microphone-device')).toBe('amd-direct')
  })

  it('waits for the user and prefers the Groq recorder when browser recognition exists', async () => {
    let recognitionInstance: {
      lang: string
      continuous: boolean
      interimResults: boolean
      start: ReturnType<typeof vi.fn>
    } | undefined

    class FakeSpeechRecognition {
      lang = ''
      continuous = false
      interimResults = false
      onstart: (() => void) | null = null
      start = vi.fn(() => this.onstart?.())
      stop = vi.fn()

      constructor() {
        recognitionInstance = this
      }
    }

    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop: vi.fn() }] }))
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    })

    class FakeMediaRecorder {
      static isTypeSupported = vi.fn(() => true)
      state: 'inactive' | 'recording' = 'inactive'
      ondataavailable: ((event: { data: Blob }) => void) | null = null
      onstop: (() => void) | null = null
      onerror: (() => void) | null = null
      start = vi.fn(() => { this.state = 'recording' })
      stop = vi.fn(() => { this.state = 'inactive' })
    }

    class FakeAudioContext {
      createAnalyser() {
        return {
          fftSize: 32,
          getByteTimeDomainData(data: Uint8Array) { data.fill(128) },
        }
      }
      createMediaStreamSource() {
        return { connect: vi.fn(), disconnect: vi.fn() }
      }
      close = vi.fn(async () => undefined)
    }

    Object.defineProperty(window, 'SpeechRecognition', {
      configurable: true,
      value: FakeSpeechRecognition,
    })
    Object.defineProperty(window, 'MediaRecorder', { configurable: true, value: FakeMediaRecorder })
    Object.defineProperty(window, 'AudioContext', { configurable: true, value: FakeAudioContext })

    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Nhập câu hỏi hoặc bấm mic để nói'))

    expect(recognitionInstance).toBeUndefined()
    expect(getUserMedia).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Sẵn sàng')

    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    await vi.waitFor(() => expect(getUserMedia).toHaveBeenCalledWith({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    }))

    expect(recognitionInstance).toBeUndefined()
    expect(wrapper.text()).toContain('Đang nghe')
  })

  it('sends a quick prompt into the conversation', async () => {
    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')

    await wrapper.get('button[data-quick-prompt="Giá Coca Zero"]')?.trigger('click')

    expect(apiMocks.respond).toHaveBeenCalledWith(
      'session-test', 'Giá Coca Zero', 'vi', expect.any(String),
    )
    await vi.waitFor(() => expect(wrapper.text()).toContain('Coca-Cola Zero hiện có giá'))
    expect(apiMocks.createSynthesisTicket).not.toHaveBeenCalled()
  })

  it('shows the safe customer-care response in the chat after a typed request', async () => {
    apiMocks.respond.mockResolvedValueOnce({
      session_id: 'session-test',
      text: 'Mình chưa thể ghi nhận phản ánh hay liên hệ nhân viên thay bạn. Bạn báo trực tiếp với nhân viên tại quầy nhé.',
      model: 'local-policy',
      grounded_product_ids: [],
      state: 'BROWSING',
    })
    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')

    await wrapper.get('textarea').setValue('Tôi muốn phàn nàn về thái độ nhân viên.')
    await wrapper.get('.composer').trigger('submit')

    expect(apiMocks.respond).toHaveBeenCalledWith(
      'session-test',
      'Tôi muốn phàn nàn về thái độ nhân viên.',
      'vi',
      expect.any(String),
    )
    await vi.waitFor(() =>
      expect(wrapper.text()).toContain('Mình chưa thể ghi nhận phản ánh'),
    )
    expect(wrapper.text()).toContain('Tôi muốn phàn nàn về thái độ nhân viên.')
    expect(apiMocks.createSynthesisTicket).not.toHaveBeenCalled()
  })

  it('frames the snack quick prompt as a catalog-backed recommendation', async () => {
    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')

    await wrapper
      .get('button[data-quick-prompt="Gợi ý snack không cay"]')
      .trigger('click')

    expect(apiMocks.respond).toHaveBeenCalledWith(
      'session-test',
      'Gợi ý snack không cay',
      'vi',
      expect.any(String),
    )
  })

  it('adds a catalog-backed product to the demo cart from its product card', async () => {
    const emptyCart = {
      cart_id: 'cart-test', session_id: 'session-test', items: [], item_count: 0,
      subtotal_vnd: 0, total_vnd: 0, version: 1, sandbox: true, data_source: 'DEMO_CATALOG',
    }
    const product = {
      product_id: 'COKE_ZERO_330', name: 'Coca-Cola Zero 330ml', category: 'drink',
      brand: 'Coca-Cola', price_vnd: 13_000, stock: 16, uom: 'lon',
      data_timestamp: '2026-10-19T00:00:00Z',
    }
    const cart = {
      ...emptyCart,
      items: [{
        product_id: product.product_id, name: product.name, category: product.category,
        brand: product.brand, unit_price_vnd: product.price_vnd, quantity: 1,
        line_total_vnd: product.price_vnd, available_stock: 15, uom: product.uom,
        data_timestamp: product.data_timestamp,
      }],
      item_count: 1, subtotal_vnd: 13_000, total_vnd: 13_000, version: 2,
    }
    apiMocks.getCart.mockResolvedValueOnce(emptyCart)
    apiMocks.respond.mockResolvedValueOnce({
      session_id: 'session-test', text: 'Mình tìm thấy Coca-Cola Zero.', model: 'catalog',
      grounded_product_ids: [product.product_id], state: 'BROWSING', products: [product],
    })
    apiMocks.addCartItem.mockResolvedValueOnce(cart)

    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
    await wrapper.get('button[data-quick-prompt="Giá Coca Zero"]').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Coca-Cola Zero 330ml'))

    await wrapper.get('.message-product-card .small-action-button').trigger('click')
    await vi.waitFor(() => expect(apiMocks.addCartItem).toHaveBeenCalledWith(
      'session-test', product.product_id, 1, expect.any(String),
    ))
    await vi.waitFor(() => expect(wrapper.text()).toContain('13.000đ'))
    expect(wrapper.text()).toContain('Giỏ hàng demo')
  })

  it('completes the kiosk product-to-sandbox-payment flow through the rendered controls', async () => {
    const expiresAt = new Date(Date.now() + 30 * 60 * 1000).toISOString()
    const product = {
      product_id: 'COKE_ZERO_330', name: 'Coca-Cola Zero 330ml', category: 'drink',
      brand: 'Coca-Cola', price_vnd: 13_000, stock: 16, uom: 'lon',
      data_timestamp: '2026-10-19T00:00:00Z',
    }
    const emptyCart = {
      cart_id: 'cart-test', session_id: 'session-test', items: [], item_count: 0,
      subtotal_vnd: 0, total_vnd: 0, version: 1, sandbox: true, data_source: 'DEMO_CATALOG',
    }
    const filledCart = {
      ...emptyCart,
      items: [{
        product_id: product.product_id, name: product.name, category: product.category,
        brand: product.brand, unit_price_vnd: product.price_vnd, quantity: 1,
        line_total_vnd: product.price_vnd, available_stock: 15, uom: product.uom,
        data_timestamp: product.data_timestamp,
      }],
      item_count: 1, subtotal_vnd: 13_000, total_vnd: 13_000, version: 2,
    }
    const quote = {
      confirmation_token: 'quote-token-test', cart: filledCart, expires_at: expiresAt, sandbox: true,
    }
    const pendingOrder = {
      order_id: 'order-test', session_id: 'session-test', cart_id: 'cart-test',
      items: filledCart.items, amount_vnd: 13_000, order_status: 'AWAITING_PAYMENT',
      payment_status: 'PENDING', created_at: expiresAt, expires_at: expiresAt,
      sandbox: true, provider_transaction_id: null,
    }
    const paidOrder = {
      ...pendingOrder, order_status: 'PAID', payment_status: 'PAID',
      provider_transaction_id: 'SANDBOX-ORDERTEST',
    }
    apiMocks.createSession.mockResolvedValueOnce({
      session_id: 'session-test', kiosk_id: 'kiosk-01', state: 'BROWSING', expires_at: expiresAt,
    }).mockResolvedValueOnce({
      session_id: 'session-next', kiosk_id: 'kiosk-01', state: 'BROWSING', expires_at: expiresAt,
    })
    apiMocks.getCart.mockResolvedValueOnce(emptyCart)
    apiMocks.respond.mockResolvedValueOnce({
      session_id: 'session-test', text: 'Mình tìm thấy Coca-Cola Zero.', model: 'catalog',
      grounded_product_ids: [product.product_id], state: 'BROWSING', products: [product],
    })
    apiMocks.addCartItem.mockResolvedValueOnce(filledCart)
    apiMocks.prepareCheckout.mockResolvedValueOnce(quote)
    apiMocks.confirmCheckout.mockResolvedValueOnce(pendingOrder)
    apiMocks.simulateSandboxPayment.mockResolvedValueOnce(paidOrder)

    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalledTimes(1))
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
    await wrapper.get('button[data-quick-prompt="Giá Coca Zero"]').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain(product.name))
    await wrapper.get('.message-product-card .small-action-button').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Giỏ hàng demo'))
    await wrapper.get('.shopping-actions .primary').trigger('click')
    await vi.waitFor(() => expect(apiMocks.prepareCheckout).toHaveBeenCalledWith('session-test'))
    await wrapper.get('.shopping-actions .primary').trigger('click')
    await vi.waitFor(() => expect(apiMocks.confirmCheckout).toHaveBeenCalledWith(
      'session-test', 'quote-token-test', expect.any(String),
    ))
    await wrapper.get('.shopping-actions .primary').trigger('click')
    await vi.waitFor(() => expect(apiMocks.simulateSandboxPayment).toHaveBeenCalledWith(
      'session-test', 'order-test', 'PAID', expect.any(String),
    ))
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalledTimes(2))

    await vi.waitFor(() => expect(wrapper.text()).toContain('Thanh toán mô phỏng hoàn tất'))
    expect(wrapper.text()).not.toContain('SANDBOX-ORDERTEST')
    expect(wrapper.findAll('.shopping-cart-line')).toHaveLength(0)
  })

  it('shows an inline recovery message when the agent fails', async () => {
    apiMocks.respond.mockRejectedValueOnce(new Error('network down'))
    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')

    await wrapper.get('button[data-quick-prompt="Giá Coca Zero"]')?.trigger('click')

    await vi.waitFor(() => expect(wrapper.text()).toContain('Chưa kết nối được trợ lý'))
    expect(wrapper.get('button[aria-label="Thử lại"]')).toBeTruthy()
  })
})
