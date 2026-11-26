import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App.vue'

const apiMocks = vi.hoisted(() => ({
  createSession: vi.fn(),
  createRequestId: vi.fn(() => 'request-test-id'),
  closeSession: vi.fn().mockResolvedValue({}),
  prewarmSpeech: vi.fn().mockResolvedValue({ warmed: true, provider: 'azure-speech' }),
  respond: vi.fn(),
  transcribe: vi.fn(),
  createSynthesisTicket: vi.fn(),
  getCart: vi.fn().mockResolvedValue(null),
  getManagerNotifierStatus: vi.fn().mockResolvedValue({
    enabled: false,
    store_name: 'Thanh Lam Mart',
    primary_channel: null,
    primary_provider: null,
    priority: ['zalo_bot', 'zalo_oa', 'zalo_webhook', 'messenger', 'gmail'],
    configured_providers: [],
    channels: {},
  }),
  addCartItem: vi.fn(),
  setCartItemQuantity: vi.fn(),
  prepareCheckout: vi.fn(),
  confirmCheckout: vi.fn(),
  cancelCheckout: vi.fn(),
  simulateSandboxPayment: vi.fn(),
  submitAssistedOrder: vi.fn(),
  listAssistedOrders: vi.fn().mockResolvedValue([]),
  getPayOSStatus: vi.fn().mockResolvedValue({
    enabled: false,
    provider: 'payos',
    store_name: 'Thanh Lam Mart',
  }),
  syncPayOSPayment: vi.fn(),
  cancelPayOSPayment: vi.fn().mockResolvedValue({}),
  browseProductCatalog: vi.fn().mockResolvedValue({
    total: 0,
    total_catalog: 0,
    offset: 0,
    limit: 24,
    has_more: false,
    active_department: null,
    active_category: null,
    departments: [],
    items: [],
    data_timestamp: new Date().toISOString(),
  }),
  fetchProductCatalogIndex: vi.fn().mockResolvedValue({
    total: 0,
    data_source: 'thanh_lam_json_snapshot',
    stock_is_current: false,
    data_timestamp: new Date().toISOString(),
    departments: [],
    rows: [],
  }),
}))

const vadMocks = vi.hoisted(() => ({ stream: null as MediaStream | null }))

vi.mock('./api', () => apiMocks)
vi.mock('@ricky0123/vad-web', () => ({
  MicVAD: {
    new: async (options: {
      getStream: () => Promise<MediaStream>
      pauseStream: (stream: MediaStream) => Promise<void>
    }) => ({
      start: async () => { vadMocks.stream = await options.getStream() },
      pause: async () => {
        if (vadMocks.stream) await options.pauseStream(vadMocks.stream)
        vadMocks.stream = null
      },
      destroy: async () => undefined,
    }),
  },
}))

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
    window.sessionStorage.removeItem('retail-ai-assisted-cart-v1')
    window.sessionStorage.removeItem('retail-ai-pending-payos-v1')
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
    Reflect.deleteProperty(window, 'MediaRecorder')
    Reflect.deleteProperty(window, 'AudioContext')
    Reflect.deleteProperty(navigator, 'mediaDevices')
    window.localStorage.removeItem('retail-ai-microphone-device')
    window.sessionStorage.removeItem('retail-ai-assisted-cart-v1')
    window.sessionStorage.removeItem('retail-ai-pending-payos-v1')
  })

  it('keeps the text drawer closed while showing only the avatar controls', async () => {
    const wrapper = mount(App)

    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await vi.waitFor(() => expect(wrapper.get('section[aria-label="Avatar Mai"]')).toBeTruthy())

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

  it('aborts an active answer request when the kiosk component is unloaded', async () => {
    const pendingResponse = deferred<{
      session_id: string
      text: string
      model: string
      grounded_product_ids: string[]
      state: string
    }>()
    apiMocks.respond.mockReturnValueOnce(pendingResponse.promise)
    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
    await wrapper.get('button[data-quick-prompt="Giá Coca Zero"]').trigger('click')
    await vi.waitFor(() => expect(apiMocks.respond).toHaveBeenCalledTimes(1))
    const requestSignal = apiMocks.respond.mock.calls[0][5] as AbortSignal

    wrapper.unmount()
    expect(requestSignal.aborted).toBe(true)
    pendingResponse.resolve({
      session_id: 'session-test', text: 'Coca Zero có giá 13.000đ.', model: 'catalog',
      grounded_product_ids: [], state: 'BROWSING',
    })
    await flushPromises()
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
      'session-test', 'Giá Coca Zero', 'vi', expect.any(String), expect.any(Function),
      expect.any(AbortSignal),
    ))
    expect(apiMocks.createSession).toHaveBeenCalledTimes(1)
  })

  it('keeps rejected typed text editable after session expiry without replaying it', async () => {
    const expiresAt = new Date(Date.now() + 30 * 60 * 1000).toISOString()
    apiMocks.createSession.mockResolvedValueOnce({ session_id: 'old-session', expires_at: expiresAt })
      .mockResolvedValueOnce({ session_id: 'new-session', expires_at: expiresAt })
    apiMocks.respond.mockRejectedValueOnce(Object.assign(new Error('Session not found'), { status: 404 }))
    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalledOnce())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
    const text = 'Chọn đồ rửa chén dưới 40 nghìn giúp mình'
    await wrapper.get('#assistant-input').setValue(text)
    await wrapper.get('form').trigger('submit')
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalledTimes(2))
    await vi.waitFor(() => expect(wrapper.get<HTMLTextAreaElement>('#assistant-input').element.value).toBe(text))
    expect(apiMocks.respond).toHaveBeenCalledOnce()
    expect(wrapper.findAll('.message-row')).toHaveLength(0)
    expect(wrapper.text()).toContain('giữ câu vừa nhập')
    apiMocks.respond.mockResolvedValueOnce({
      session_id: 'new-session', text: 'Mình tư vấn trong phiên mới.', model: 'test',
      grounded_product_ids: [], state: 'BROWSING',
    })
    await wrapper.get('form').trigger('submit')
    await vi.waitFor(() => expect(apiMocks.respond).toHaveBeenCalledTimes(2))
    expect(apiMocks.respond.mock.calls[1][0]).toBe('new-session')
    expect(apiMocks.respond.mock.calls[1][1]).toBe(text)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Mình tư vấn trong phiên mới.'))
    wrapper.unmount()
  })

  it('does not restore typed text into another customer session after an explicit reset', async () => {
    let rejectResponse!: (reason: Error) => void
    apiMocks.respond.mockReturnValueOnce(new Promise((_, reject) => { rejectResponse = reject }))
    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalledOnce())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
    await wrapper.get('#assistant-input').setValue('Lựa chọn riêng của khách trước')
    await wrapper.get('form').trigger('submit')
    await vi.waitFor(() => expect(apiMocks.respond).toHaveBeenCalledOnce())
    await wrapper.get('.end-session-button').trigger('click')
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalledTimes(2))
    rejectResponse(Object.assign(new Error('Session not found'), { status: 404 }))
    await flushPromises()
    expect(wrapper.get<HTMLTextAreaElement>('#assistant-input').element.value).toBe('')
    expect(wrapper.findAll('.message-row')).toHaveLength(0)
    expect(apiMocks.respond).toHaveBeenCalledOnce()
    wrapper.unmount()
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
    const requestSignal = apiMocks.respond.mock.calls[0][5] as AbortSignal
    expect(wrapper.text()).toContain('Giá Coca Zero')

    await wrapper.get('.end-session-button').trigger('click')
    expect(requestSignal.aborted).toBe(true)
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

  it('rejects an agent response whose session id does not match the active session', async () => {
    apiMocks.respond.mockResolvedValueOnce({
      session_id: 'different-session',
      text: 'Nội dung không thuộc phiên hiện tại.',
      model: 'catalog',
      grounded_product_ids: [],
      state: 'BROWSING',
      cart: {
        cart_id: 'other-cart', session_id: 'different-session', items: [], item_count: 0,
        subtotal_vnd: 0, total_vnd: 0, version: 1, sandbox: true, data_source: 'DEMO_CATALOG',
      },
    })
    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
    await wrapper.get('textarea').setValue('Giá Coca Zero')
    await wrapper.get('.composer').trigger('submit')

    await vi.waitFor(() => expect(wrapper.text()).toContain(
      'Câu trả lời không thuộc phiên hội thoại hiện tại.',
    ))
    expect(wrapper.text()).not.toContain('Nội dung không thuộc phiên hiện tại.')
    expect(wrapper.text()).not.toContain('other-cart')
    expect(wrapper.findAll('.message-assistant')).toHaveLength(0)
  })

  it('keeps the kiosk in Vietnamese and preserves Latin brand names', async () => {
    const wrapper = mount(App)

    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
    expect(wrapper.find('button[aria-label="English"]').exists()).toBe(false)
    expect(wrapper.find('button[aria-label="中文"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Tiếng Việt')
    expect(document.documentElement.lang).toBe('vi-VN')
    await wrapper.get('textarea').setValue('Coca-Cola Zero bao nhiêu tiền?')
    await wrapper.get('.composer').trigger('submit')
    await vi.waitFor(() =>
      expect(apiMocks.respond).toHaveBeenCalledWith(
        'session-test',
        'Coca-Cola Zero bao nhiêu tiền?',
        'vi',
        expect.any(String),
        expect.any(Function),
        expect.any(AbortSignal),
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
      expect.any(Function),
      expect.any(AbortSignal),
    ))
    await vi.waitFor(() => expect(wrapper.find('.support-ticket-card').exists()).toBe(true))
    expect(wrapper.get('.support-ticket-card').text()).not.toContain('ticket-demo-1')
    expect(wrapper.get('.support-ticket-card').text()).toContain('Chỉ là bản demo')
  })

  it('calls manager via the floating button and displays delivered Zalo alert card', async () => {
    apiMocks.getManagerNotifierStatus.mockResolvedValueOnce({
      enabled: true,
      store_name: 'Thanh Lam Mart',
      primary_channel: 'zalo',
      primary_provider: 'zalo_bot',
      priority: ['zalo_bot', 'messenger', 'gmail'],
      configured_providers: ['zalo_bot'],
      channels: { zalo_bot: true },
    })
    apiMocks.respond.mockResolvedValueOnce({
      session_id: 'session-test',
      text: 'Dạ, mình đã gửi tin nhắn qua Zalo báo quản lý biết bạn đang cần gặp rồi ạ. Quản lý sẽ ra hỗ trợ bạn ngay nhé.',
      model: 'manager-notifier',
      grounded_product_ids: [],
      tools_used: ['handoff_to_staff', 'notify_manager_zalo'],
      state: 'HANDOFF',
      support_ticket: {
        ticket_id: 'a1b2c3d4-0000-0000-0000-000000000001',
        session_id: 'session-test',
        kiosk_id: 'kiosk-01',
        issue_type: 'OTHER',
        description: 'Mình muốn gọi quản lý giúp mình.',
        status: 'NOTIFIED_ZALO',
        created_at: new Date().toISOString(),
        demo: false,
        target_role: 'manager',
        notified_channel: 'zalo',
        notified_provider: 'zalo_bot',
        notification_status: 'DELIVERED',
      },
    })
    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Gọi quản lý"]').trigger('click')

    await vi.waitFor(() => expect(apiMocks.respond).toHaveBeenCalledWith(
      'session-test',
      'Mình muốn gọi quản lý giúp mình.',
      'vi',
      expect.any(String),
      expect.any(Function),
      expect.any(AbortSignal),
    ))
    await vi.waitFor(() => expect(wrapper.find('.support-ticket-card.is-delivered').exists()).toBe(true))
    expect(wrapper.get('.support-ticket-card').text()).toContain('Đã gửi tin báo quản lý qua Zalo')
    expect(wrapper.get('.support-ticket-card').text()).not.toContain('NOTIFIED_ZALO')
    expect(wrapper.get('.support-ticket-card').text()).not.toContain('a1b2c3d4')
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

  it('uses a saved microphone before labels are exposed by the browser', async () => {
    window.localStorage.setItem('retail-ai-microphone-device', 'saved-physical-mic')
    window.localStorage.setItem('retail-ai-microphone-explicit-choice', '1')
    const getUserMedia = vi.fn(async () => ({
      getTracks: () => [{ stop: vi.fn(), label: 'Saved physical microphone' }],
    }))
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        enumerateDevices: vi.fn(async () => [
          { deviceId: 'saved-physical-mic', groupId: 'saved', kind: 'audioinput', label: '' },
        ]),
        getUserMedia,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      },
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
      state = 'running'
      createAnalyser() {
        return {
          fftSize: 512,
          getByteTimeDomainData(data: Uint8Array) { data.fill(128) },
        }
      }
      createMediaStreamSource() { return { connect: vi.fn(), disconnect: vi.fn() } }
      close = vi.fn(async () => undefined)
    }
    Object.defineProperty(window, 'MediaRecorder', { configurable: true, value: FakeMediaRecorder })
    Object.defineProperty(window, 'AudioContext', { configurable: true, value: FakeAudioContext })

    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')

    await vi.waitFor(() => expect(getUserMedia).toHaveBeenCalledOnce())
    expect(getUserMedia).toHaveBeenCalledWith({ audio: expect.objectContaining({
      deviceId: { exact: 'saved-physical-mic' },
    }) })
    wrapper.unmount()
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
        channelCount: { ideal: 1 }, sampleRate: { ideal: 48000 }, sampleSize: { ideal: 16 }, voiceIsolation: true,
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
      'session-test', 'Giá Coca Zero', 'vi', expect.any(String), expect.any(Function),
      expect.any(AbortSignal),
    )
    await vi.waitFor(() => expect(wrapper.text()).toContain('Coca-Cola Zero hiện có giá'))
    expect(apiMocks.createSynthesisTicket).not.toHaveBeenCalled()
  })

  it('renders response deltas progressively after the backend response is validated', async () => {
    const pendingResponse = deferred<{
      session_id: string
      text: string
      model: string
      grounded_product_ids: string[]
      state: string
    }>()
    let emitTextDelta: ((text: string) => void) | undefined
    apiMocks.respond.mockImplementationOnce((...args: unknown[]) => {
      const callback = args[4]
      if (typeof callback !== 'function') throw new Error('Missing response delta callback')
      emitTextDelta = callback as (text: string) => void
      return pendingResponse.promise
    })

    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
    await wrapper.get('button[data-quick-prompt="Giá Coca Zero"]').trigger('click')
    await vi.waitFor(() => expect(emitTextDelta).toBeDefined())

    expect(wrapper.find('.message-assistant .message-bubble').text()).toContain('Mình đang tìm câu trả lời')
    emitTextDelta?.('Coca-Cola Zero ')
    await flushPromises()
    expect(wrapper.find('.message-assistant .message-bubble').text()).toBe('Coca-Cola Zero')
    expect(wrapper.find('.message-model').exists()).toBe(false)

    emitTextDelta?.('có giá 13.000đ.')
    await flushPromises()
    expect(wrapper.find('.message-assistant .message-bubble').text()).toBe(
      'Coca-Cola Zero có giá 13.000đ.',
    )
    pendingResponse.resolve({
      session_id: 'session-test',
      text: 'Coca-Cola Zero có giá 13.000đ.',
      model: 'catalog',
      grounded_product_ids: ['COKE_ZERO_330'],
      state: 'BROWSING',
    })
    await flushPromises()
    expect(wrapper.find('.message-model').exists()).toBe(false)
    expect(wrapper.find('.message-assistant .message-bubble').text()).toBe(
      'Coca-Cola Zero có giá 13.000đ.',
    )
  })

  it('reuses the same client turn id on retry and does not duplicate the customer message', async () => {
    apiMocks.respond.mockRejectedValueOnce(new Error('network down')).mockResolvedValueOnce({
      session_id: 'session-test',
      text: 'Coca-Cola Zero hiện có giá 13.000đ.',
      model: 'catalog',
      grounded_product_ids: ['COKE_ZERO_330'],
      state: 'BROWSING',
    })
    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
    await wrapper.get('textarea').setValue('Giá Coca Zero')
    await wrapper.get('.composer').trigger('submit')
    await vi.waitFor(() => expect(wrapper.text()).toContain('network down'))
    const firstTurnId = apiMocks.respond.mock.calls[0][3]

    await wrapper.get('.error-retry').trigger('click')

    await vi.waitFor(() => expect(wrapper.find('.message-assistant .message-bubble').text())
      .toBe('Coca-Cola Zero hiện có giá 13.000đ.'))
    expect(apiMocks.respond).toHaveBeenCalledTimes(2)
    expect(apiMocks.respond.mock.calls[1][3]).toBe(firstTurnId)
    expect(wrapper.findAll('.message-user')).toHaveLength(1)
    expect(wrapper.findAll('.message-assistant')).toHaveLength(1)
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
      expect.any(Function),
      expect.any(AbortSignal),
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
      expect.any(Function),
      expect.any(AbortSignal),
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
      data_source: 'demo_product_catalog',
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
    expect(wrapper.text()).toContain('Catalog demo · 19/10/2026')
    expect(wrapper.text()).toContain('Giỏ hàng demo')
  })

  it('completes the kiosk product-to-sandbox-payment flow through the rendered controls', async () => {
    const expiresAt = new Date(Date.now() + 30 * 60 * 1000).toISOString()
    const product = {
      product_id: 'COKE_ZERO_330', name: 'Coca-Cola Zero 330ml', category: 'drink',
      brand: 'Coca-Cola', price_vnd: 13_000, stock: 16, uom: 'lon',
      data_timestamp: '2026-10-19T00:00:00Z',
      data_source: 'demo_product_catalog',
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

  it('opens a product photo popup when clicking a recommended product card and closes cleanly', async () => {
    const chinsu = {
      product_id: 'TLM_10141747',
      name: 'Tương ớt  Chin Su chai 1kg',
      category: 'Tương ớt',
      brand: 'CHIN-SU',
      price_vnd: 62_200,
      list_price_vnd: 72_000,
      promotion: 'Giảm 14%',
      image_url: 'https://s3-hcmc02.higiocloud.vn/images/2024/11/10141747-20241117094121.jpg',
      stock: 0,
      stock_is_current: false,
      uom: 'chai',
      data_timestamp: '2026-08-31T00:00:00Z',
      data_source: 'thanh_lam_json_snapshot',
      demo_units_sold_30d: 284,
      sales_data_source: 'SIMULATED' as const,
    }
    apiMocks.respond.mockResolvedValueOnce({
      session_id: 'session-test',
      text: 'Đây là Tương ớt Chin Su chai 1kg.',
      model: 'catalog',
      grounded_product_ids: [chinsu.product_id],
      state: 'BROWSING',
      products: [chinsu],
    })

    const wrapper = mount(App, { attachTo: document.body })
    try {
      await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
      await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
      await wrapper.get('button[data-quick-prompt="Giá Coca Zero"]').trigger('click')
      await vi.waitFor(() => expect(wrapper.find('.message-product-card').exists()).toBe(true))

      // Clicking the details dropdown should NOT open the modal.
      await wrapper.get('.message-product-card details summary').trigger('click')
      expect(document.body.querySelector('.product-modal')).toBeNull()

      // Clicking the product card opens the photo lightbox popup.
      await wrapper.get('.message-product-card').trigger('click')
      const modal = document.body.querySelector('.product-modal')
      expect(modal).not.toBeNull()
      const img = modal?.querySelector<HTMLImageElement>('.product-modal-image')
      expect(img?.getAttribute('src')).toBe(chinsu.image_url)
      expect(modal?.textContent).toContain('Tương ớt Chin Su chai 1kg')
      expect(modal?.textContent).toContain('62.200đ')
      expect(modal?.textContent).toContain('72.000đ')
      expect(modal?.textContent).toContain('Giảm 14%')

      // Pressing Escape closes the popup.
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await flushPromises()
      expect(document.body.querySelector('.product-modal')).toBeNull()

      // Re-open and click "Hỏi thành phần" to trigger a follow-up question.
      apiMocks.respond.mockResolvedValueOnce({
        session_id: 'session-test',
        text: 'Thành phần gồm nước, đường, ớt 110 g/kg...',
        model: 'catalog',
        grounded_product_ids: [chinsu.product_id],
        state: 'BROWSING',
        products: [chinsu],
      })
      await wrapper.get('.message-product-card').trigger('click')
      const askBtn = document.body.querySelector<HTMLButtonElement>('.product-modal-ask-btn')
      expect(askBtn).not.toBeNull()
      askBtn?.click()
      await flushPromises()
      expect(document.body.querySelector('.product-modal')).toBeNull()
      await vi.waitFor(() => expect(apiMocks.respond).toHaveBeenCalledWith(
        'session-test',
        'Thành phần chính của Tương ớt Chin Su chai 1kg là gì?',
        'vi',
        expect.any(String),
        expect.any(Function),
        expect.any(AbortSignal),
      ))
    } finally {
      wrapper.unmount()
    }
  })

  it('adds snapshot catalog products to the assisted cart and sends a pick-and-handover order to the manager', async () => {
    const chinsu = {
      product_id: 'TLM_10141747',
      name: 'Tương ớt Chin Su chai 1kg',
      category: 'Tương ớt',
      brand: 'CHIN-SU',
      price_vnd: 62_200,
      stock: 407,
      stock_is_current: false,
      uom: 'Chai',
      image_url: 'https://hcm.fstorage.vn/images/2026/01/chinsu-1kg.jpg',
      data_timestamp: '2026-09-15T13:53:30Z',
      data_source: 'thanh_lam_json_snapshot',
    }
    apiMocks.getManagerNotifierStatus.mockResolvedValueOnce({
      enabled: true,
      store_name: 'Thanh Lam Mart',
      primary_channel: 'zalo',
      primary_provider: 'zalo_bot',
      priority: ['zalo_bot'],
      configured_providers: ['zalo_bot'],
      channels: { zalo_bot: true },
    })
    apiMocks.respond.mockResolvedValueOnce({
      session_id: 'session-test',
      text: 'Mình tìm thấy Tương ớt Chin Su chai 1kg giá tham khảo 62.200đ.',
      model: 'catalog',
      grounded_product_ids: [chinsu.product_id],
      state: 'BROWSING',
      products: [chinsu],
    })
    apiMocks.submitAssistedOrder.mockResolvedValueOnce({
      order_id: 'order-assisted-1',
      order_code: 'TLM-998877',
      session_id: 'session-test',
      kiosk_id: 'kiosk-01',
      store_name: 'Thanh Lam Mart',
      items: [{
        product_id: chinsu.product_id,
        name: chinsu.name,
        brand: chinsu.brand,
        category: chinsu.category,
        uom: chinsu.uom,
        image_url: chinsu.image_url,
        unit_price_vnd: chinsu.price_vnd,
        quantity: 2,
        line_total_vnd: 124_400,
        shelf_location: 'A05-B02-L03-P01',
        snapshot_stock: 407,
        stock_is_current: false,
      }],
      item_count: 2,
      subtotal_vnd: 124_400,
      total_vnd: 124_400,
      customer_name: 'Anh Minh',
      pickup_location: 'Đứng tại Kiosk',
      customer_note: 'Chọn chai hạn mới',
      status: 'SENT_TO_STAFF',
      ticket_id: 'ticket-assisted-1',
      created_at: new Date().toISOString(),
      data_source: 'thanh_lam_json_snapshot',
      stock_is_current: false,
      notified_channel: 'zalo',
      notified_provider: 'zalo_bot',
      notification_status: 'DELIVERED',
      customer_message: 'Dạ, mình đã gửi đơn hỗ trợ mã #TLM-998877 (2 sản phẩm, tạm tính 124.400đ) qua Zalo tới quản lý cửa hàng rồi ạ.',
    })

    const wrapper = mount(App)
    try {
      await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
      await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
      await wrapper.get('button[data-quick-prompt="Giá Coca Zero"]').trigger('click')
      await vi.waitFor(() => expect(wrapper.text()).toContain('Tương ớt Chin Su chai 1kg'))

      // Add from product card and increment to 2 via inline stepper
      await wrapper.get('.card-cart-btn').trigger('click')
      await vi.waitFor(() => expect(wrapper.get('.card-stepper-qty').text()).toBe('1'))
      await wrapper.get('button[aria-label="Tăng số lượng Tương ớt Chin Su chai 1kg"]').trigger('click')
      await vi.waitFor(() => expect(wrapper.get('.card-stepper-qty').text()).toBe('2'))

      // Sticky cart bar reflects 2 items and total 124.400đ, opens CartDrawer
      expect(wrapper.get('.assisted-cart-bar').text()).toContain('2 sản phẩm trong giỏ')
      expect(wrapper.get('.assisted-cart-bar').text()).toContain('124.400đ')
      await wrapper.get('.assisted-cart-bar-btn').trigger('click')
      await flushPromises()

      const sheet = document.body.querySelector('.cart-sheet')
      expect(sheet).not.toBeNull()
      expect(sheet?.textContent).toContain('Giỏ hàng của bạn')
      expect(sheet?.textContent).toContain('Đặt mua · Gửi quản lý (Zalo)')

      const nameInput = sheet?.querySelector<HTMLInputElement>('input[autocomplete="name"]')
      const noteInput = sheet?.querySelector<HTMLInputElement>('input[maxlength="300"]')
      if (nameInput) {
        nameInput.value = 'Anh Minh'
        nameInput.dispatchEvent(new Event('input'))
      }
      if (noteInput) {
        noteInput.value = 'Chọn chai hạn mới'
        noteInput.dispatchEvent(new Event('input'))
      }
      await flushPromises()

      const cta = sheet?.querySelector<HTMLButtonElement>('.cart-checkout-cta')
      expect(cta).not.toBeNull()
      cta?.click()
      await flushPromises()

      await vi.waitFor(() => expect(apiMocks.submitAssistedOrder).toHaveBeenCalledWith(
        'session-test',
        expect.objectContaining({
          items: [{ product_id: chinsu.product_id, quantity: 2 }],
          customer_name: 'Anh Minh',
          pickup_location: 'Đứng tại Kiosk',
          customer_note: 'Chọn chai hạn mới',
        }),
      ))

      // Confirmation receipt rendered in drawer + chat history card
      await vi.waitFor(() => expect(document.body.querySelector('.cart-receipt-card')?.textContent).toContain('#TLM-998877'))
      expect(document.body.querySelector('.cart-receipt-card')?.textContent).toContain('A05-B02-L03-P01')
      expect(wrapper.text()).toContain('Đơn hỗ trợ #TLM-998877')
    } finally {
      wrapper.unmount()
    }
  })

  it('supports PayOS QR payment flow and updates receipt + chat card once PAID', async () => {
    const sting = {
      product_id: 'TLM_STING_330',
      name: 'Nước tăng lực Sting dâu chai 330ml',
      category: 'Đồ uống',
      brand: 'Sting',
      uom: 'Chai',
      price_vnd: 10_500,
      stock: 120,
      stock_is_current: false,
      data_source: 'thanh_lam_json_snapshot',
      data_timestamp: '2026-09-15T00:00:00Z',
      image_url: 'https://example.com/sting.jpg',
    }
    apiMocks.getManagerNotifierStatus.mockResolvedValueOnce({
      enabled: true,
      store_name: 'Thanh Lam Mart',
      primary_channel: 'zalo',
      primary_provider: 'zalo_bot',
      priority: ['zalo_bot'],
      configured_providers: ['zalo_bot'],
      channels: { zalo: { configured: true, provider: 'zalo_bot' } },
    })
    apiMocks.getPayOSStatus.mockResolvedValueOnce({
      enabled: true,
      provider: 'payos',
      store_name: 'Thanh Lam Mart',
    })
    apiMocks.respond.mockResolvedValueOnce({
      session_id: 'session-test',
      text: 'Sting dâu 330ml giá 10.500đ.',
      model: 'snapshot-fastpath',
      grounded_product_ids: [sting.product_id],
      state: 'BROWSING',
      products: [sting],
    })
    const pendingPayOSOrder = {
      order_id: 'order-payos-1',
      order_code: 'TLM-PAY001',
      session_id: 'session-test',
      kiosk_id: 'kiosk-01',
      store_name: 'Thanh Lam Mart',
      items: [{
        product_id: sting.product_id,
        name: sting.name,
        brand: sting.brand,
        category: sting.category,
        uom: sting.uom,
        image_url: sting.image_url,
        unit_price_vnd: sting.price_vnd,
        quantity: 2,
        line_total_vnd: 21_000,
        shelf_location: 'A02-B01-L02-P04',
        snapshot_stock: 120,
        stock_is_current: false,
      }],
      item_count: 2,
      subtotal_vnd: 21_000,
      total_vnd: 21_000,
      customer_name: 'Anh Huy',
      pickup_location: 'Đứng tại Kiosk',
      status: 'AWAITING_PAYMENT',
      payment_method: 'payos',
      payment_status: 'PENDING_PAYOS',
      payos_order_code: 987654321,
      payos_payment_link_id: 'plink-001',
      payos_checkout_url: 'https://pay.payos.vn/web/plink-001',
      payos_qr_code: '00020101021238570010A000000727012700069704220113VQRQ00012345653037045405210005802VN6304ABCD',
      payos_account_number: '19036888999',
      payos_account_name: 'THANH LAM MART',
      payos_description: 'TLMPAY001',
      payment_verify_token: 'signed-verify-token-xyz',
      ticket_id: 'ticket-payos-1',
      created_at: new Date().toISOString(),
      data_source: 'thanh_lam_json_snapshot',
      stock_is_current: false,
      notification_status: 'PENDING_PAYMENT',
      customer_message: 'Vui lòng quét mã QR PayOS để thanh toán 21.000đ.',
    }
    const paidPayOSOrder = {
      ...pendingPayOSOrder,
      status: 'SENT_TO_STAFF',
      payment_status: 'PAID',
      payos_transaction_ref: 'FT261126001',
      paid_at: new Date().toISOString(),
      notified_channel: 'zalo',
      notified_provider: 'zalo_bot',
      notification_status: 'DELIVERED',
      customer_message: 'Đã xác nhận thanh toán PayOS 21.000đ cho đơn #TLM-PAY001 và đã báo quản lý qua Zalo soạn hàng ngay!',
    }
    apiMocks.submitAssistedOrder.mockResolvedValueOnce(pendingPayOSOrder)
    apiMocks.syncPayOSPayment.mockResolvedValueOnce(paidPayOSOrder)

    const wrapper = mount(App)
    try {
      await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
      await vi.waitFor(() => expect(apiMocks.getPayOSStatus).toHaveBeenCalled())
      await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')
      await wrapper.get('button[data-quick-prompt="Giá Coca Zero"]').trigger('click')
      await vi.waitFor(() => expect(wrapper.text()).toContain('Nước tăng lực Sting dâu chai 330ml'))

      await wrapper.get('.card-cart-btn').trigger('click')
      await wrapper.get('button[aria-label="Tăng số lượng Nước tăng lực Sting dâu chai 330ml"]').trigger('click')
      await wrapper.get('.assisted-cart-bar-btn').trigger('click')
      await flushPromises()

      const sheet = document.body.querySelector('.cart-sheet')
      expect(sheet?.textContent).toContain('Chuyển khoản QR PayOS')
      expect(sheet?.textContent).toContain('Thanh toán QR PayOS · 21.000đ')

      const cta = sheet?.querySelector<HTMLButtonElement>('.cart-checkout-cta')
      cta?.click()
      await flushPromises()

      await vi.waitFor(() => expect(apiMocks.submitAssistedOrder).toHaveBeenCalledWith(
        'session-test',
        expect.objectContaining({
          payment_method: 'payos',
          items: [{ product_id: sting.product_id, quantity: 2 }],
        }),
      ))

      // Live PayOS QR payment panel appears with bank details
      await vi.waitFor(() => expect(document.body.querySelector('.payos-qr-panel')).not.toBeNull())
      const qrPanel = document.body.querySelector('.payos-qr-panel')
      expect(qrPanel?.textContent).toContain('#TLM-PAY001')
      expect(qrPanel?.textContent).toContain('19036888999')
      expect(qrPanel?.textContent).toContain('TLMPAY001')
      expect(qrPanel?.querySelector('a.payos-external-btn')?.getAttribute('href')).toBe('https://pay.payos.vn/web/plink-001')

      // Manual check triggers syncPayOSPayment and transitions to PAID receipt + chat card
      const checkBtn = qrPanel?.querySelector<HTMLButtonElement>('.cart-primary-btn')
      expect(checkBtn).not.toBeNull()
      checkBtn?.click()
      await flushPromises()

      await vi.waitFor(() => expect(apiMocks.syncPayOSPayment).toHaveBeenCalledWith(987654321, 'signed-verify-token-xyz'))
      await vi.waitFor(() => expect(document.body.querySelector('.cart-receipt-card')?.textContent).toContain('Đã thanh toán PayOS · Đã báo Zalo'))
      expect(document.body.querySelector('.cart-receipt-card')?.textContent).toContain('FT261126001')
      expect(wrapper.text()).toContain('Đơn đã thanh toán #TLM-PAY001')
      expect(wrapper.text()).toContain('Đã TT PayOS · Đã báo Zalo')
    } finally {
      wrapper.unmount()
    }
  })

  it('opens all-products catalog modal by category from corner button and adds to cart', async () => {
    const snack = {
      product_id: 'TLM_SNACK_01',
      name: 'Bánh Snack khoai tây Lay\'s vị tự nhiên 52g',
      category: 'Bánh Snack',
      brand: 'LAY\'S',
      uom: 'Gói',
      price_vnd: 12_500,
      list_price_vnd: 15_000,
      stock: 40,
      stock_is_current: false,
      data_source: 'thanh_lam_json_snapshot',
      data_timestamp: '2026-09-15T00:00:00Z',
      image_url: 'https://example.com/lays.jpg',
    }
    const drink = {
      product_id: 'TLM_COKE_01',
      name: 'Nước ngọt Coca-Cola Zero lon 320ml',
      category: 'Nước Ngọt',
      brand: 'COCA-COLA',
      uom: 'Lon',
      price_vnd: 10_000,
      stock: 80,
      stock_is_current: false,
      data_source: 'thanh_lam_json_snapshot',
      data_timestamp: '2026-09-15T00:00:00Z',
      image_url: 'https://example.com/coke.jpg',
    }
    const departments = [
      {
        name: 'Bánh kẹo',
        count: 473,
        subcategories: [
          { name: 'Kẹo - Chocolate', count: 167 },
          { name: 'Bánh Snack', count: 93 },
        ],
      },
      {
        name: 'Đồ uống, thuốc lá',
        count: 327,
        subcategories: [{ name: 'Nước Ngọt', count: 158 }],
      },
    ]
    apiMocks.browseProductCatalog.mockImplementation(async (opts?: { department?: string }) => {
      if (opts?.department === 'Bánh kẹo') {
        return {
          total: 473,
          total_catalog: 3081,
          offset: 0,
          limit: 24,
          has_more: true,
          active_department: 'Bánh kẹo',
          active_category: null,
          departments,
          items: [snack],
          data_timestamp: '2026-09-15T00:00:00Z',
        }
      }
      return {
        total: 3081,
        total_catalog: 3081,
        offset: 0,
        limit: 24,
        has_more: true,
        active_department: null,
        active_category: null,
        departments,
        items: [drink, snack],
        data_timestamp: '2026-09-15T00:00:00Z',
      }
    })

    const wrapper = mount(App)
    try {
      await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
      await wrapper.get('.corner-action-btn.is-catalog').trigger('click')
      await flushPromises()

      await vi.waitFor(() => expect(document.body.querySelector('.catalog-modal')).not.toBeNull())
      const modal = document.body.querySelector('.catalog-modal')!
      expect(modal.textContent).toContain('Tất cả sản phẩm')
      expect(modal.textContent).toContain('Bánh kẹo')
      expect(modal.textContent).toContain('Đồ uống, thuốc lá')

      // Click department "Bánh kẹo" and verify subcategory chips + filtered card appear
      const deptButtons = Array.from(modal.querySelectorAll<HTMLButtonElement>('.catalog-dept-btn'))
      const snackDeptBtn = deptButtons.find((btn) => btn.textContent?.includes('Bánh kẹo'))
      expect(snackDeptBtn).toBeTruthy()
      snackDeptBtn?.click()
      await flushPromises()

      await vi.waitFor(() => expect(apiMocks.browseProductCatalog).toHaveBeenCalledWith(
        expect.objectContaining({ department: 'Bánh kẹo' }),
      ))
      await vi.waitFor(() => expect(modal.textContent).toContain('Bánh Snack'))

      // Add item directly from catalog grid and verify corner cart badge + CartDrawer handoff
      const addBtn = modal.querySelector<HTMLButtonElement>('.catalog-add-btn')
      expect(addBtn).not.toBeNull()
      addBtn?.click()
      await flushPromises()

      await vi.waitFor(() => expect(wrapper.get('.corner-cart-badge').text()).toBe('1'))
      const cartBarBtn = modal.querySelector<HTMLButtonElement>('.catalog-cart-bar .assisted-cart-bar-btn')
      expect(cartBarBtn).not.toBeNull()
      cartBarBtn?.click()
      await flushPromises()

      await vi.waitFor(() => expect(document.body.querySelector('.cart-sheet')?.textContent).toContain('Lay\'s'))
    } finally {
      wrapper.unmount()
    }
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
