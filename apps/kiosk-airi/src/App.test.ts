import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App.vue'

const apiMocks = vi.hoisted(() => ({
  createSession: vi.fn(),
  respond: vi.fn(),
  transcribe: vi.fn(),
  createSynthesisTicket: vi.fn(),
}))

vi.mock('./api', () => apiMocks)

describe('kiosk app', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.documentElement.lang = 'vi'
    window.localStorage.removeItem('retail-ai-microphone-device')
    apiMocks.createSession.mockResolvedValue({
      session_id: 'session-test',
      kiosk_id: 'kiosk-01',
      state: 'BROWSING',
      expires_at: '2026-08-31T13:00:00Z',
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

    expect(apiMocks.respond).toHaveBeenCalledWith('session-test', 'Giá Coca Zero', 'vi')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Coca-Cola Zero hiện có giá'))
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
    )
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
