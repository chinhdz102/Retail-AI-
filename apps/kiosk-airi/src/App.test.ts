import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App.vue'

const apiMocks = vi.hoisted(() => ({
  createSession: vi.fn(),
  respond: vi.fn(),
  transcribe: vi.fn(),
}))

vi.mock('./api', () => apiMocks)

describe('kiosk app', () => {
  beforeEach(() => {
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

  it('starts continuous Vietnamese listening when the browser supports it', async () => {
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

    Object.defineProperty(window, 'SpeechRecognition', {
      configurable: true,
      value: FakeSpeechRecognition,
    })

    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await vi.waitFor(() => expect(recognitionInstance?.start).toHaveBeenCalled())

    expect(recognitionInstance?.lang).toBe('vi-VN')
    expect(recognitionInstance?.continuous).toBe(true)
    expect(wrapper.text()).toContain('Đang nghe')
  })

  it('automatically starts the recorder fallback when SpeechRecognition is unavailable', async () => {
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

    Object.defineProperty(window, 'MediaRecorder', { configurable: true, value: FakeMediaRecorder })
    Object.defineProperty(window, 'AudioContext', { configurable: true, value: FakeAudioContext })

    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await vi.waitFor(() => expect(getUserMedia).toHaveBeenCalledWith({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    }))

    await vi.waitFor(() => expect(wrapper.text()).toContain('Đang nghe'))
  })

  it('sends a quick prompt into the conversation', async () => {
    const wrapper = mount(App)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalled())
    await wrapper.get('button[aria-label="Mở phần chữ"]').trigger('click')

    await wrapper.get('button[data-quick-prompt="Giá Coca Zero"]')?.trigger('click')

    expect(apiMocks.respond).toHaveBeenCalledWith('session-test', 'Giá Coca Zero', 'vi')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Coca-Cola Zero hiện có giá'))
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
