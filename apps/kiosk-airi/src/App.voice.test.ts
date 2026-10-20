import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

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

const controllerStore = vi.hoisted(() => ({ current: null as any }))

vi.mock('./api', () => apiMocks)
vi.mock('./voice-controller', () => ({
  VoiceConversationController: class FakeVoiceConversationController {
    options: any
    isActive = false

    constructor(options: any) {
      this.options = options
      controllerStore.current = this
    }

    setLocale() {}

    async start() {
      this.startCalls = (this.startCalls ?? 0) + 1
      this.isActive = true
      this.options.onState({
        mode: this.options.preferRecorder ? 'recorder' : 'recognition',
        phase: 'listening',
        permission: 'granted',
      })
    }

    startCalls = 0

    pause() {
      this.isActive = false
      this.options.onState({ mode: 'idle', phase: 'idle', permission: 'granted' })
    }

    stop() { this.pause() }
    stopCapture() { this.pause() }
  },
}))

import App from './App.vue'

describe('voice transcript integration', () => {
  it('does not replay microphone audio into a new customer session after expiry', async () => {
    vi.clearAllMocks()
    const expiresAt = new Date(Date.now() + 30 * 60 * 1000).toISOString()
    apiMocks.createSession.mockResolvedValueOnce({ session_id: 'old-session', expires_at: expiresAt })
      .mockResolvedValueOnce({ session_id: 'recovered-session', expires_at: expiresAt })
    apiMocks.transcribe.mockRejectedValueOnce(Object.assign(new Error('Session not found'), { status: 404 }))
    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    const audio = new Blob(['microphone audio'])
    controllerStore.current.options.onAudio(audio)
    await vi.waitFor(() => expect(apiMocks.createSession).toHaveBeenCalledTimes(2))
    await vi.waitFor(() => expect(wrapper.text()).toContain('Phiên trước đã kết thúc'))
    expect(apiMocks.createSession).toHaveBeenCalledTimes(2)
    expect(apiMocks.transcribe).toHaveBeenNthCalledWith(1, 'old-session', audio, 'vi')
    expect(apiMocks.transcribe).toHaveBeenCalledTimes(1)
    expect(apiMocks.respond).not.toHaveBeenCalled()
    expect(wrapper.findAll('.message-row')).toHaveLength(0)
  })

  it('discards a pending transcription after the user stops listening', async () => {
    vi.clearAllMocks()
    apiMocks.createSession.mockResolvedValue({
      session_id: 'session-test',
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    let resolveTranscript!: (value: unknown) => void
    apiMocks.transcribe.mockImplementationOnce(() =>
      new Promise((resolve) => { resolveTranscript = resolve }),
    )
    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    controllerStore.current.options.onAudio(new Blob(['voice']))
    await vi.waitFor(() => expect(apiMocks.transcribe).toHaveBeenCalledTimes(1))
    await wrapper.get('button[aria-label="Dừng nghe"]').trigger('click')
    resolveTranscript({ session_id: 'session-test', text: 'Hai chai nước', language: 'vi' })
    await vi.waitFor(() => expect(wrapper.get('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
    expect(apiMocks.respond).not.toHaveBeenCalled()
    expect(wrapper.findAll('.message-user')).toHaveLength(0)
    wrapper.unmount()
  })

  it('discards a late voice agent answer after the user stops listening', async () => {
    vi.clearAllMocks()
    apiMocks.createSession.mockResolvedValue({
      session_id: 'session-test',
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    let resolveAgent!: (value: unknown) => void
    apiMocks.respond.mockImplementationOnce(() =>
      new Promise((resolve) => { resolveAgent = resolve }),
    )
    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    controllerStore.current.options.onTranscript('Giá coca bao nhiêu?')
    await vi.waitFor(() => expect(apiMocks.respond).toHaveBeenCalledTimes(1))
    await wrapper.get('button[aria-label="Dừng nghe"]').trigger('click')
    resolveAgent({
      session_id: 'session-test', text: 'Coca giá 13.000đ.',
      state: 'BROWSING', model: 'mock', grounded_product_ids: [],
    })
    await vi.waitFor(() => expect(wrapper.findAll('.message-assistant')).toHaveLength(0))
    expect(apiMocks.createSynthesisTicket).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('keeps transcription errors visible when hands-free listening resumes', async () => {
    vi.clearAllMocks()
    apiMocks.createSession.mockResolvedValue({ session_id: 'session-test' })
    apiMocks.transcribe.mockRejectedValue(new Error('Không nhận diện được tiếng nói: thử lại'))
    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    controllerStore.current.options.onAudio(new Blob(['voice']))
    await vi.waitFor(() => expect(controllerStore.current.startCalls).toBe(2))
    expect(wrapper.get('.chat-error').text()).toContain('Không nhận diện được tiếng nói: thử lại')
    expect(wrapper.get('.voice-mode-chip').text()).toContain('AUTO')
    expect(apiMocks.createSession).toHaveBeenCalledTimes(1)
  })

  afterEach(() => {
    controllerStore.current = null
  })

  it('sends a recorder transcript to Qwen after Whisper finishes', async () => {
    apiMocks.createSession.mockResolvedValue({
      session_id: 'session-test',
      kiosk_id: 'kiosk-01',
      state: 'BROWSING',
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    apiMocks.transcribe.mockResolvedValue({
      session_id: 'session-test',
      text: 'Cho tôi biết giá Coca Zero.',
      language: 'vi',
      model: 'whisper-large-v3-turbo',
    })
    apiMocks.respond.mockResolvedValue({
      session_id: 'session-test',
      text: 'Coca Zero giá 13.000đ.',
      model: 'qwen/qwen3.8-27b',
      grounded_product_ids: ['COKE_ZERO_330'],
      state: 'BROWSING',
    })
    apiMocks.createSynthesisTicket.mockRejectedValue(new Error('tts unavailable'))

    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.get('button[aria-label="Bắt đầu nói"]')).toBeTruthy())
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    await vi.waitFor(() => expect(controllerStore.current).not.toBeNull())
    expect(wrapper.get('button[aria-label="Đóng phần chữ"]')).toBeTruthy()

    controllerStore.current.options.onAudio(new Blob(['voice']))

    await vi.waitFor(() => expect(apiMocks.transcribe).toHaveBeenCalledWith(
      'session-test',
      expect.any(Blob),
      'vi',
    ))
    await vi.waitFor(() => expect(apiMocks.respond).toHaveBeenCalledWith(
      'session-test',
      'Cho tôi biết giá Coca Zero.',
      'vi',
      expect.any(String),
      expect.any(Function),
      expect.any(AbortSignal),
    ))
    await vi.waitFor(() => expect(wrapper.get('.message-user .message-bubble').text()).toBe('Cho tôi biết giá Coca Zero.'))
    await vi.waitFor(() => expect(wrapper.text()).toContain('Coca Zero giá 13.000đ.'))
  })

  it('plays cloud TTS from the one-use streaming URL', async () => {
    const play = vi.fn().mockResolvedValue(undefined)
    const pause = vi.fn()
    Object.defineProperty(HTMLMediaElement.prototype, 'play', {
      configurable: true,
      value: play,
    })
    Object.defineProperty(HTMLMediaElement.prototype, 'pause', {
      configurable: true,
      value: pause,
    })
    apiMocks.createSession.mockResolvedValue({
      session_id: 'session-test',
      kiosk_id: 'kiosk-01',
      state: 'BROWSING',
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    apiMocks.respond.mockResolvedValue({
      session_id: 'session-test',
      text: 'Coca Zero giá 13.000đ.',
      model: 'qwen/qwen3.8-27b',
      grounded_product_ids: ['COKE_ZERO_330'],
      state: 'BROWSING',
    })
    apiMocks.createSynthesisTicket.mockResolvedValue(
      'http://127.0.0.1:8000/audio/synthesize/stream/one-use-token',
    )

    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.get('button[aria-label="Bắt đầu nói"]')).toBeTruthy())
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    await vi.waitFor(() => expect(controllerStore.current).not.toBeNull())
    controllerStore.current.options.onTranscript('Cho tôi biết giá Coca Zero.')

    await vi.waitFor(() => expect(apiMocks.createSynthesisTicket).toHaveBeenCalledWith(
      'session-test',
      'Coca Zero giá 13.000đ.',
      'vi',
    ))
    await vi.waitFor(() => expect(wrapper.get('audio').element.getAttribute('src')).toBe(
      'http://127.0.0.1:8000/audio/synthesize/stream/one-use-token',
    ))
    expect(play).toHaveBeenCalled()
  })

  it('unlocks browser speech audio when the user starts voice mode', async () => {
    const resume = vi.fn()
    let spokenUtterance: any
    Object.defineProperty(window, 'speechSynthesis', {
      configurable: true,
      value: {
        resume,
        cancel: vi.fn(),
        getVoices: () => [],
        speak: (utterance: { onstart?: () => void; onend?: () => void; rate?: number }) => {
          spokenUtterance = utterance
          utterance.onstart?.()
          utterance.onend?.()
        },
      },
    })
    Object.defineProperty(window, 'SpeechSynthesisUtterance', {
      configurable: true,
      value: class {
        text: string
        onstart: (() => void) | null = null
        onend: (() => void) | null = null
        onerror: (() => void) | null = null
        lang = ''
        rate = 1
        pitch = 1
        voice = null

        constructor(text: string) {
          this.text = text
        }
      },
    })
    apiMocks.createSession.mockResolvedValue({
      session_id: 'session-test',
      kiosk_id: 'kiosk-01',
      state: 'BROWSING',
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    apiMocks.createSynthesisTicket.mockRejectedValue(new Error('tts unavailable'))

    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.get('button[aria-label="Bắt đầu nói"]')).toBeTruthy())
    expect(spokenUtterance).toBeUndefined()
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')

    expect(resume).toHaveBeenCalled()
    expect(spokenUtterance).toBeUndefined()
  })

  it('unlocks a real audio element from the voice-mode click', async () => {
    const play = vi.fn().mockResolvedValue(undefined)
    const pause = vi.fn()
    Object.defineProperty(HTMLMediaElement.prototype, 'play', {
      configurable: true,
      value: play,
    })
    Object.defineProperty(HTMLMediaElement.prototype, 'pause', {
      configurable: true,
      value: pause,
    })
    apiMocks.createSession.mockResolvedValue({
      session_id: 'session-test',
      kiosk_id: 'kiosk-01',
      state: 'BROWSING',
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    apiMocks.createSynthesisTicket.mockRejectedValue(new Error('tts unavailable'))

    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.get('button[aria-label="Bắt đầu nói"]')).toBeTruthy())
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')

    expect(play).toHaveBeenCalled()
  })

  it('ignores a cancelled browser utterance callback from an older voice turn', async () => {
    const utterances: Array<{ onstart?: () => void; onend?: () => void; onerror?: () => void }> = []
    let activeUtterance: typeof utterances[number] | null = null
    Object.defineProperty(window, 'speechSynthesis', {
      configurable: true,
      value: {
        resume: vi.fn(),
        cancel: vi.fn(() => activeUtterance?.onerror?.()),
        getVoices: () => [],
        speak: (utterance: typeof utterances[number]) => {
          activeUtterance = utterance
          utterances.push(utterance)
          utterance.onstart?.()
        },
      },
    })
    Object.defineProperty(window, 'SpeechSynthesisUtterance', {
      configurable: true,
      value: class {
        text: string
        onstart: (() => void) | null = null
        onend: (() => void) | null = null
        onerror: (() => void) | null = null
        lang = ''
        rate = 1
        pitch = 1
        voice = null

        constructor(text: string) {
          this.text = text
        }
      },
    })
    apiMocks.createSession.mockResolvedValue({
      session_id: 'session-test',
      kiosk_id: 'kiosk-01',
      state: 'BROWSING',
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    apiMocks.createSynthesisTicket.mockRejectedValue(new Error('tts unavailable'))
    apiMocks.respond.mockResolvedValue({
      session_id: 'session-test',
      text: 'Mình đang kiểm tra giúp bạn.',
      model: 'qwen/qwen3.8-27b',
      grounded_product_ids: [],
      state: 'BROWSING',
    })

    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.get('button[aria-label="Bắt đầu nói"]')).toBeTruthy())
    expect(utterances).toHaveLength(0)
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    await vi.waitFor(() => expect(controllerStore.current).not.toBeNull())
    controllerStore.current.options.onTranscript('Cho tôi hỏi một chút')
    await vi.waitFor(() => expect(utterances).toHaveLength(1))
    controllerStore.current.options.onTranscript('Và thêm một câu nữa')
    await vi.waitFor(() => expect(utterances).toHaveLength(2))

    expect(controllerStore.current.startCalls).toBe(1)
    utterances[0].onend?.()
    expect(controllerStore.current.startCalls).toBe(1)
    utterances[1].onend?.()
    expect(controllerStore.current.startCalls).toBe(2)
  })
})
