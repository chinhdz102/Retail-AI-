import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  createSession: vi.fn(),
  createRequestId: vi.fn(() => 'request-test-id'),
  closeSession: vi.fn().mockResolvedValue({}),
  prewarmSpeech: vi.fn().mockResolvedValue({ warmed: true, provider: 'azure-speech' }),
  getAzureSpeechToken: vi.fn().mockResolvedValue({ enabled: false }),
  respond: vi.fn(),
  transcribe: vi.fn(),
  createSynthesisTicket: vi.fn(),
  getCart: vi.fn().mockResolvedValue(null),
  getManagerNotifierStatus: vi.fn().mockResolvedValue({
    enabled: false,
    store_name: 'Thanh Lam Mart',
    primary_channel: null,
    primary_provider: null,
    priority: [],
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
  cancelPayOSPayment: vi.fn(),
  browseProductCatalog: vi.fn().mockResolvedValue({
    total: 0, total_catalog: 0, offset: 0, limit: 24, has_more: false,
    active_department: null, active_category: null, departments: [], items: [],
    data_timestamp: new Date().toISOString(),
  }),
  fetchProductCatalogIndex: vi.fn().mockResolvedValue({
    total: 0, data_source: 'thanh_lam_json_snapshot', stock_is_current: false,
    data_timestamp: new Date().toISOString(), departments: [], rows: [],
  }),
}))

const controllerStore = vi.hoisted(() => ({ current: null as any }))

vi.mock('./api', () => apiMocks)
vi.mock('./voice-controller', () => ({
  VoiceConversationController: class FakeVoiceConversationController {
    options: any
    isActive = false
    bargeInMode = false

    constructor(options: any) {
      this.options = options
      controllerStore.current = this
    }

    setLocale() {}

    setBargeInMode(enabled: boolean) {
      this.bargeInMode = enabled
    }

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
      this.bargeInMode = false
      this.options.onState({ mode: 'idle', phase: 'idle', permission: 'granted' })
    }

    stop() { this.pause() }
    stopCapture() { this.pause() }
  },
}))

import App from './App.vue'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

function setupCloudReply() {
  vi.clearAllMocks()
  const play = vi.fn().mockResolvedValue(undefined)
  const pause = vi.fn()
  const videoPlay = vi.fn().mockResolvedValue(undefined)
  const videoPause = vi.fn()
  Object.defineProperty(HTMLAudioElement.prototype, 'play', { configurable: true, value: play })
  Object.defineProperty(HTMLAudioElement.prototype, 'pause', { configurable: true, value: pause })
  Object.defineProperty(HTMLVideoElement.prototype, 'play', { configurable: true, value: videoPlay })
  Object.defineProperty(HTMLVideoElement.prototype, 'pause', { configurable: true, value: videoPause })
  apiMocks.createSession.mockResolvedValue({ session_id: 'session-test' })
  apiMocks.respond.mockResolvedValue({
    session_id: 'session-test', text: 'Mình gợi ý chai nước này.',
    grounded_product_ids: [], state: 'BROWSING',
  })
  apiMocks.createSynthesisTicket.mockResolvedValue('http://127.0.0.1:8000/audio/synthesize/stream/current')
  return { play, pause }
}

async function startSpokenReply() {
  const wrapper = mount(App)
  await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
  await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
  controllerStore.current.options.onTranscript('Có nước gì ít ngọt không?')
  await vi.waitFor(() => expect(apiMocks.createSynthesisTicket).toHaveBeenCalledOnce())
  return wrapper
}

afterEach(() => {
  window.history.replaceState({}, '', '/')
  Reflect.deleteProperty(HTMLAudioElement.prototype, 'play')
  Reflect.deleteProperty(HTMLAudioElement.prototype, 'pause')
  Reflect.deleteProperty(HTMLVideoElement.prototype, 'play')
  Reflect.deleteProperty(HTMLVideoElement.prototype, 'pause')
})

describe('voice turn ownership', () => {
  it('keeps uncertain Vietnamese words editable without sending a shopping request', async () => {
    setupCloudReply()
    apiMocks.transcribe.mockResolvedValueOnce({
      session_id: 'session-test', text: 'Lấy sữa đường', language: 'vi',
      speech_detected: true, needs_confirmation: true,
    })
    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    expect(controllerStore.current.options.locale).toBe('vi-VN')
    controllerStore.current.options.onAudio(new Blob(['voice']))
    await vi.waitFor(() => expect(
      (wrapper.get('textarea').element as HTMLTextAreaElement).value,
    ).toBe('Lấy sữa đường'))
    expect(apiMocks.respond).not.toHaveBeenCalled()
    expect(apiMocks.createSynthesisTicket).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Mình nghe chưa chắc câu này')
    await wrapper.get('textarea').setValue('Lấy sữa không đường')
    await wrapper.get('form.composer').trigger('submit')
    await vi.waitFor(() => expect(apiMocks.respond).toHaveBeenCalledWith(
      'session-test', 'Lấy sữa không đường', 'vi', expect.any(String),
      expect.any(Function), expect.any(AbortSignal),
    ))
    wrapper.unmount()
  })

  it('stops old cloud speech and ignores its ended callback during a typed request', async () => {
    const { pause } = setupCloudReply()
    const wrapper = await startSpokenReply()
    const audio = wrapper.get('audio').element as HTMLAudioElement
    await vi.waitFor(() => expect(audio.getAttribute('src')).toContain('/stream/current'))
    const staleEnded = audio.onended
    const answer = deferred<any>()
    apiMocks.respond.mockReturnValueOnce(answer.promise)
    const mic = controllerStore.current
    const starts = mic.startCalls
    pause.mockClear()
    await wrapper.get('textarea').setValue('Tìm loại khác giúp tôi')
    await wrapper.get('form.composer').trigger('submit')
    expect(pause).toHaveBeenCalledOnce()
    expect(audio.getAttribute('src')).toBeNull()
    staleEnded?.call(audio, new Event('ended'))
    await vi.waitFor(() => expect(wrapper.get('.voice-feedback strong').text()).toBe('Đang xử lý'))
    expect(mic.startCalls).toBe(starts)
    answer.resolve({ session_id: 'session-test', text: 'Có loại khác.', grounded_product_ids: [], state: 'BROWSING' })
    wrapper.unmount()
  })

  it.each(['typed', 'stop'] as const)('drops an old TTS ticket after a %s takeover', async (action) => {
    const { play } = setupCloudReply()
    const ticket = deferred<string>()
    apiMocks.createSynthesisTicket.mockReturnValueOnce(ticket.promise)
    const wrapper = await startSpokenReply()
    const playCalls = play.mock.calls.length
    if (action === 'typed') {
      apiMocks.respond.mockReturnValueOnce(new Promise(() => {}))
      await wrapper.get('textarea').setValue('Cho tôi hỏi thêm')
      await wrapper.get('form.composer').trigger('submit')
    } else {
      await wrapper.get('button[aria-label="Dừng nghe"]').trigger('click')
    }
    ticket.resolve('http://127.0.0.1:8000/audio/synthesize/stream/stale')
    await Promise.resolve()
    await Promise.resolve()
    await new Promise((done) => setTimeout(done, 0))
    expect(play).toHaveBeenCalledTimes(playCalls)
    expect(wrapper.get('audio').element.getAttribute('src')).toBeNull()
    if (action === 'stop') {
      await vi.waitFor(() => expect(wrapper.get('.voice-feedback strong').text()).toBe('Sẵn sàng'))
    }
    wrapper.unmount()
  })

  it('ignores a late autoplay rejection after a typed request has taken over', async () => {
    const { play } = setupCloudReply()
    const playback = deferred<void>()
    play.mockImplementation(function (this: HTMLMediaElement) {
      return this.src.includes('/stream/') ? playback.promise : Promise.resolve()
    })
    const wrapper = await startSpokenReply()
    await vi.waitFor(() => expect(wrapper.get('audio').element.getAttribute('src')).toContain('/stream/current'))
    apiMocks.respond.mockReturnValueOnce(new Promise(() => {}))
    await wrapper.get('textarea').setValue('Tư vấn tiếp giúp tôi')
    await wrapper.get('form.composer').trigger('submit')
    playback.reject(Object.assign(new Error('blocked'), { name: 'NotAllowedError' }))
    await new Promise((done) => setTimeout(done, 0))
    expect(wrapper.get('.voice-feedback strong').text()).toBe('Đang xử lý')
    wrapper.unmount()
  })

  it('cancels in-flight Vietnamese STT when the user stops listening', async () => {
    setupCloudReply()
    const transcription = deferred<any>()
    apiMocks.transcribe.mockReturnValueOnce(transcription.promise)
    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    controllerStore.current.options.onAudio(new Blob(['voice']))
    await vi.waitFor(() => expect(apiMocks.transcribe).toHaveBeenCalledOnce())
    const signal = apiMocks.transcribe.mock.calls[0][3] as AbortSignal
    await wrapper.get('button[aria-label="Dừng nghe"]').trigger('click')
    expect(signal.aborted).toBe(true)
    transcription.resolve({ session_id: 'session-test', text: 'Câu cũ', language: 'vi' })
    await new Promise((done) => setTimeout(done, 0))
    expect(wrapper.get('.voice-feedback strong').text()).toBe('Sẵn sàng')
    expect(apiMocks.respond).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('keeps the new request retry ID when an aborted voice response arrives late', async () => {
    setupCloudReply()
    apiMocks.createRequestId.mockReturnValueOnce('old-voice').mockReturnValueOnce('new-text')
      .mockReturnValueOnce('unexpected-new-id')
    const oldAnswer = deferred<any>()
    apiMocks.respond.mockReturnValueOnce(oldAnswer.promise)
    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    controllerStore.current.options.onTranscript('Có nước suối không?')
    await vi.waitFor(() => expect(apiMocks.respond).toHaveBeenCalledOnce())
    const oldSignal = apiMocks.respond.mock.calls[0][5] as AbortSignal
    await wrapper.get('button[aria-label="Dừng nghe"]').trigger('click')
    expect(oldSignal.aborted).toBe(true)
    apiMocks.respond.mockRejectedValueOnce(new Error('temporary network error'))
    await wrapper.get('textarea').setValue('Tìm đồ uống khác')
    await wrapper.get('form.composer').trigger('submit')
    await vi.waitFor(() => expect(wrapper.find('.error-retry').exists()).toBe(true))
    const newTurnId = apiMocks.respond.mock.calls[1][3]
    oldAnswer.resolve({ session_id: 'session-test', text: 'Câu trả lời cũ.', state: 'BROWSING' })
    await new Promise((done) => setTimeout(done, 0))
    apiMocks.respond.mockResolvedValueOnce({ session_id: 'session-test', text: 'Bạn xem nước này nhé.', state: 'BROWSING' })
    await wrapper.get('.error-retry').trigger('click')
    await vi.waitFor(() => expect(apiMocks.respond).toHaveBeenCalledTimes(3))
    expect(apiMocks.respond.mock.calls[2][3]).toBe(newTurnId)
    expect(wrapper.text()).not.toContain('Câu trả lời cũ.')
    wrapper.unmount()
    apiMocks.createRequestId.mockReset().mockReturnValue('request-test-id')
  })

  it('ignores a late browser speech start after cancellation', async () => {
    setupCloudReply()
    const utterances: Array<{ onstart?: () => void; onend?: () => void; onerror?: () => void }> = []
    const cancel = vi.fn()
    Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
      cancel, resume: vi.fn(), getVoices: () => [],
      speak: (utterance: typeof utterances[number]) => { utterances.push(utterance) },
    } })
    Object.defineProperty(window, 'SpeechSynthesisUtterance', { configurable: true, value: class {
      text: string
      constructor(text: string) { this.text = text }
    } })
    apiMocks.createSynthesisTicket.mockRejectedValueOnce(new Error('cloud unavailable'))
    const wrapper = await startSpokenReply()
    await vi.waitFor(() => expect(utterances).toHaveLength(1))
    apiMocks.respond.mockReturnValueOnce(new Promise(() => {}))
    cancel.mockClear()
    await wrapper.get('textarea').setValue('Tìm loại khác')
    await wrapper.get('form.composer').trigger('submit')
    expect(cancel).toHaveBeenCalledOnce()
    utterances[0].onstart?.()
    utterances[0].onend?.()
    await new Promise((done) => setTimeout(done, 0))
    expect(wrapper.get('.voice-feedback strong').text()).toBe('Đang xử lý')
    wrapper.unmount()
    Reflect.deleteProperty(window, 'speechSynthesis')
    Reflect.deleteProperty(window, 'SpeechSynthesisUtterance')
  })
})

describe('voice transcript integration', () => {
  it('prewarms speech in the background while listening without creating a chat turn', async () => {
    vi.clearAllMocks()
    apiMocks.createSession.mockResolvedValue({ session_id: 'session-test' })
    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    await vi.waitFor(() => expect(apiMocks.prewarmSpeech).toHaveBeenCalledWith('session-test', 'vi'))

    controllerStore.current.options.onState({
      mode: 'recorder',
      phase: 'recording',
      permission: 'granted',
    })

    expect(apiMocks.prewarmSpeech).toHaveBeenCalledOnce()
    expect(apiMocks.transcribe).not.toHaveBeenCalled()
    expect(apiMocks.respond).not.toHaveBeenCalled()
    expect(wrapper.findAll('.message-user')).toHaveLength(0)
    wrapper.unmount()
  })

  it('keeps listening when the speech prewarm request fails', async () => {
    vi.clearAllMocks()
    apiMocks.createSession.mockResolvedValue({ session_id: 'session-test' })
    apiMocks.prewarmSpeech.mockRejectedValueOnce(new Error('temporary prewarm failure'))
    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    await vi.waitFor(() => expect(apiMocks.prewarmSpeech).toHaveBeenCalledOnce())

    expect(wrapper.text()).toContain('Đang nghe')
    expect(wrapper.find('.chat-error').exists()).toBe(false)
    expect(apiMocks.respond).not.toHaveBeenCalled()
    wrapper.unmount()
  })

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
    expect(apiMocks.transcribe).toHaveBeenNthCalledWith(1, 'old-session', audio, 'vi', expect.any(AbortSignal), false)
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
    const transcriptionSignal = apiMocks.transcribe.mock.calls[0][3] as AbortSignal
    expect(transcriptionSignal.aborted).toBe(false)
    await wrapper.get('button[aria-label="Dừng nghe"]').trigger('click')
    expect(transcriptionSignal.aborted).toBe(true)
    resolveTranscript({ session_id: 'session-test', text: 'Hai chai nước', language: 'vi' })
    await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
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

  it('never sends a transcript from a mismatched customer session', async () => {
    vi.clearAllMocks()
    apiMocks.createSession.mockResolvedValue({
      session_id: 'session-test',
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    apiMocks.transcribe.mockResolvedValueOnce({
      session_id: 'session-other', text: 'Mua hàng', language: 'vi', model: 'mock',
    })
    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    controllerStore.current.options.onAudio(new Blob(['voice']))
    await vi.waitFor(() => expect(wrapper.text()).toContain('Phiên âm thanh không thuộc phiên'))
    expect(apiMocks.respond).not.toHaveBeenCalled()
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

  it('does not send an empty or rejected transcript to the retail agent', async () => {
    vi.clearAllMocks()
    apiMocks.createSession.mockResolvedValue({ session_id: 'session-test' })
    apiMocks.transcribe.mockResolvedValue({
      session_id: 'session-test',
      text: '',
      language: 'vi',
      model: 'whisper-large-v3',
    })
    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    controllerStore.current.options.onAudio(new Blob(['silence']))

    await vi.waitFor(() => expect(apiMocks.transcribe).toHaveBeenCalledOnce())
    expect(apiMocks.respond).not.toHaveBeenCalled()
    expect(wrapper.findAll('.message-user')).toHaveLength(0)
    await vi.waitFor(() => expect(controllerStore.current.startCalls).toBe(2))
    await vi.waitFor(() => expect(wrapper.text()).toContain('Mình chưa nghe thấy câu nói rõ ràng'))
  })

  it('does not forward provider-marked non-speech even if Whisper returned text', async () => {
    vi.clearAllMocks()
    apiMocks.createSession.mockResolvedValue({ session_id: 'session-test' })
    apiMocks.transcribe.mockResolvedValue({
      session_id: 'session-test',
      text: 'Hãy subscribe cho kênh La La School.',
      language: 'vi',
      model: 'whisper-large-v3',
      speech_detected: false,
    })
    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    controllerStore.current.options.onAudio(new Blob(['quiet room noise']))

    await vi.waitFor(() => expect(apiMocks.transcribe).toHaveBeenCalledOnce())
    expect(apiMocks.respond).not.toHaveBeenCalled()
    expect(wrapper.findAll('.message-user')).toHaveLength(0)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Mình chưa nghe thấy câu nói rõ ràng'))
  })

  it('blocks a repeated Whisper loop before it reaches the retail agent', async () => {
    vi.clearAllMocks()
    apiMocks.createSession.mockResolvedValue({ session_id: 'session-test' })
    apiMocks.transcribe.mockResolvedValue({
      session_id: 'session-test',
      text: 'Cảm ơn các bạn đã theo dõi và ủng thông, và hạn thấy nhiều thông, và hạn thấy nhiều thông.',
      language: 'vi',
      model: 'whisper-large-v3',
      speech_detected: true,
    })
    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    controllerStore.current.options.onAudio(new Blob(['misrecognized speech']))

    await vi.waitFor(() => expect(apiMocks.transcribe).toHaveBeenCalledOnce())
    expect(apiMocks.respond).not.toHaveBeenCalled()
    expect(wrapper.findAll('.message-user')).toHaveLength(0)
    await vi.waitFor(() => expect(controllerStore.current.startCalls).toBe(2))
    await vi.waitFor(() => expect(wrapper.text()).toContain('Mình chưa nghe thấy câu nói rõ ràng'))
  })

  it('blocks a repeated loop from the alternate transcript callback too', async () => {
    vi.clearAllMocks()
    apiMocks.createSession.mockResolvedValue({ session_id: 'session-test' })
    const wrapper = mount(App)
    await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
    await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')

    controllerStore.current.options.onTranscript(
      'Cảm ơn các bạn đã theo dõi và ủng thông, và hạn thấy nhiều thông, và hạn thấy nhiều thông.',
    )

    expect(apiMocks.respond).not.toHaveBeenCalled()
    expect(wrapper.findAll('.message-user')).toHaveLength(0)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Mình chưa nghe thấy câu nói rõ ràng'))
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
      expect.any(AbortSignal),
      false,
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

  it('optional ?halfDuplex=1 Speaker Echo Shield strictly pauses the mic while Mai speaks (0% self-recording) and resumes after 650ms settle or Spacebar interrupt', async () => {
    window.history.replaceState({}, '', '/?halfDuplex=1')
    const { pause } = setupCloudReply()
    const wrapper = await startSpokenReply()
    const audio = wrapper.get('audio').element as HTMLAudioElement
    await vi.waitFor(() => expect(audio.getAttribute('src')).toContain('/stream/current'))
    const mic = controllerStore.current
    // Mic MUST remain strictly paused during loudspeaker playback in half-duplex mode!
    expect(mic.isActive).toBe(false)
    expect(mic.bargeInMode).toBe(false)

    // Any stray audio/transcript while Mai speaks is strictly ignored
    mic.options.onAudio(new Blob(['speaker output']))
    mic.options.onTranscript('Mình gợi ý chai nước này.')
    expect(apiMocks.transcribe).not.toHaveBeenCalled()
    expect(apiMocks.respond).toHaveBeenCalledOnce()

    // Pressing Spacebar immediately silences Mai and resumes listening after 240ms speaker decay
    pause.mockClear()
    vi.useFakeTimers()
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', cancelable: true }))
    expect(pause).toHaveBeenCalledOnce()
    expect(audio.getAttribute('src')).toBeNull()
    expect(mic.isActive).toBe(false)
    await vi.advanceTimersByTimeAsync(239)
    expect(mic.isActive).toBe(false)
    await vi.advanceTimersByTimeAsync(1)
    expect(mic.isActive).toBe(true)
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('arms verified barge-in during cloud speech by default, stops Mai on customer speech, and rejects self-echo', async () => {
    const { pause } = setupCloudReply()
    const wrapper = await startSpokenReply()
    const audio = wrapper.get('audio').element as HTMLAudioElement
    await vi.waitFor(() => expect(audio.getAttribute('src')).toContain('/stream/current'))
    const mic = controllerStore.current
    await vi.waitFor(() => expect(mic.bargeInMode).toBe(true))
    expect(mic.isActive).toBe(true)

    // Unconfirmed stray audio callback or exact self-echo transcript must NOT interrupt Mai.
    mic.options.onAudio(new Blob(['unconfirmed loudspeaker echo']))
    mic.options.onTranscript('Mình gợi ý chai nước này.')
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.voice-feedback strong').text()).toBe('Đang trả lời')
    expect(audio.getAttribute('src')).toContain('/stream/current')
    expect(apiMocks.transcribe).not.toHaveBeenCalled()
    expect(apiMocks.respond).toHaveBeenCalledOnce()

    // Confirmed user barge-in immediately silences Mai and captures the interrupting utterance.
    pause.mockClear()
    apiMocks.transcribe.mockResolvedValueOnce({
      session_id: 'session-test',
      text: 'Khoan đã, cho mình xem nước suối thôi',
      language: 'vi',
      speech_detected: true,
    })
    apiMocks.respond.mockResolvedValueOnce({
      session_id: 'session-test',
      text: 'Mình có Aquafina 500ml giá 6.000đ.',
      grounded_product_ids: [],
      state: 'BROWSING',
    })
    mic.options.onBargeIn()
    await wrapper.vm.$nextTick()
    expect(pause).toHaveBeenCalledOnce()
    expect(audio.getAttribute('src')).toBeNull()
    expect(mic.bargeInMode).toBe(false)
    expect(wrapper.get('.voice-feedback strong').text()).toBe('Đang nghe')

    mic.options.onAudio(new Blob(['customer barge-in voice']))
    await vi.waitFor(() => expect(apiMocks.transcribe).toHaveBeenCalledOnce())
    await vi.waitFor(() => expect(apiMocks.respond).toHaveBeenCalledTimes(2))
    expect(apiMocks.respond.mock.calls[1][1]).toBe('Khoan đã, cho mình xem nước suối thôi')
    wrapper.unmount()
  })

  it('discards transcribed assistant loudspeaker echo after barge-in without answering itself', async () => {
    setupCloudReply()
    const wrapper = await startSpokenReply()
    const audio = wrapper.get('audio').element as HTMLAudioElement
    await vi.waitFor(() => expect(audio.getAttribute('src')).toContain('/stream/current'))
    const mic = controllerStore.current
    await vi.waitFor(() => expect(mic.bargeInMode).toBe(true))

    apiMocks.transcribe.mockResolvedValueOnce({
      session_id: 'session-test',
      text: 'Mình gợi ý chai nước này.',
      language: 'vi',
      speech_detected: true,
    })
    mic.options.onBargeIn()
    mic.options.onAudio(new Blob(['bleedthrough echo']))
    await vi.waitFor(() => expect(apiMocks.transcribe).toHaveBeenCalledOnce())
    await vi.waitFor(() => expect(wrapper.get('.voice-feedback strong').text()).toBe('Đang nghe'))
    expect(apiMocks.respond).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('guards the 650ms loudspeaker tail after speech ends before disarming barge-in to open mic', async () => {
    setupCloudReply()
    const wrapper = await startSpokenReply()
    const audio = wrapper.get('audio').element as HTMLAudioElement
    await vi.waitFor(() => expect(audio.onended).toBeTypeOf('function'))
    const mic = controllerStore.current
    await vi.waitFor(() => expect(mic.bargeInMode).toBe(true))
    vi.useFakeTimers()
    audio.onended?.call(audio, new Event('ended'))
    mic.options.onAudio(new Blob(['reverberation']))
    mic.options.onTranscript('Tiếng vọng cuối câu')
    await vi.advanceTimersByTimeAsync(649)
    expect(mic.bargeInMode).toBe(true)
    expect(apiMocks.transcribe).not.toHaveBeenCalled()
    expect(apiMocks.respond).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(1)
    expect(mic.isActive).toBe(true)
    expect(mic.bargeInMode).toBe(false)
    expect(wrapper.get('.voice-feedback strong').text()).toBe('Đang nghe')
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('allows the stop-speaking button to interrupt and resume after the speaker tail', async () => {
    const { pause } = setupCloudReply()
    const wrapper = await startSpokenReply()
    await vi.waitFor(() => expect(wrapper.get('audio').element.getAttribute('src')).toContain('/stream/current'))
    pause.mockClear()
    vi.useFakeTimers()
    await wrapper.get('button[aria-label="Dừng Mai nói"]').trigger('click')
    expect(pause).toHaveBeenCalledOnce()
    expect(wrapper.get('audio').element.getAttribute('src')).toBeNull()
    expect(controllerStore.current.isActive).toBe(false)
    await vi.advanceTimersByTimeAsync(240)
    expect(controllerStore.current.isActive).toBe(true)
    wrapper.unmount()
    vi.useRealTimers()
  })

  it.each(['stop', 'typed', 'unmount'] as const)('cancels a pending microphone restart on %s', async (action) => {
    setupCloudReply()
    const wrapper = await startSpokenReply()
    const audio = wrapper.get('audio').element as HTMLAudioElement
    await vi.waitFor(() => expect(audio.onended).toBeTypeOf('function'))
    const mic = controllerStore.current
    const startsBeforeEnd = mic.startCalls
    vi.useFakeTimers()
    audio.onended?.call(audio, new Event('ended'))
    if (action === 'stop') {
      await wrapper.get('button[aria-label="Dừng nghe"]').trigger('click')
    } else if (action === 'typed') {
      apiMocks.respond.mockReturnValueOnce(new Promise(() => {}))
      await wrapper.get('textarea').setValue('Câu hỏi mới')
      await wrapper.get('form.composer').trigger('submit')
    } else {
      wrapper.unmount()
    }
    await vi.advanceTimersByTimeAsync(750)
    expect(mic.startCalls).toBe(startsBeforeEnd)
    expect(mic.isActive).toBe(false)
    if (action !== 'unmount') wrapper.unmount()
    vi.useRealTimers()
  })

  it.each(['end', 'error'] as const)('keeps mic paused during browser TTS in ?halfDuplex=1 Speaker Echo Shield and resumes cleanly after %s', async (event) => {
    window.history.replaceState({}, '', '/?halfDuplex=1')
    setupCloudReply()
    let utterance: any
    vi.stubGlobal('speechSynthesis', {
      resume: vi.fn(), cancel: vi.fn(), getVoices: () => [],
      speak: (value: any) => { utterance = value; value.onstart?.() },
    })
    vi.stubGlobal('SpeechSynthesisUtterance', class {
      text: string
      constructor(text: string) { this.text = text }
    })
    apiMocks.createSynthesisTicket.mockRejectedValueOnce(new Error('tts unavailable'))
    const wrapper = await startSpokenReply()
    await vi.waitFor(() => expect(utterance).toBeDefined())
    const mic = controllerStore.current
    expect(mic.isActive).toBe(false)
    expect(mic.bargeInMode).toBe(false)
    mic.options.onAudio(new Blob(['browser TTS echo']))
    expect(apiMocks.transcribe).not.toHaveBeenCalled()
    vi.useFakeTimers()
    utterance[event === 'end' ? 'onend' : 'onerror']()
    await vi.advanceTimersByTimeAsync(649)
    expect(mic.isActive).toBe(false)
    await vi.advanceTimersByTimeAsync(1)
    expect(mic.isActive).toBe(true)
    expect(mic.bargeInMode).toBe(false)
    wrapper.unmount()
    vi.useRealTimers()
    vi.unstubAllGlobals()
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
    expect(controllerStore.current.bargeInMode).toBe(true)
    const startsBeforeStop = controllerStore.current.startCalls
    await wrapper.get('button[aria-label="Dừng Mai nói"]').trigger('click')
    await vi.waitFor(() => expect(controllerStore.current.startCalls).toBe(startsBeforeStop + 1))
    controllerStore.current.options.onTranscript('Và thêm một câu nữa')
    await vi.waitFor(() => expect(utterances).toHaveLength(2))
    expect(controllerStore.current.bargeInMode).toBe(true)

    // Stale end event from the cancelled first utterance must not disarm barge-in during the second turn.
    utterances[0].onend?.()
    expect(controllerStore.current.bargeInMode).toBe(true)
    utterances[1].onend?.()
    await vi.waitFor(() => expect(controllerStore.current.bargeInMode).toBe(false), { timeout: 2_000 })
    wrapper.unmount()
  })

  it('discards short tone-distorted and price-unit echoes right after Mai finishes speaking', async () => {
    setupCloudReply()
    apiMocks.respond.mockResolvedValueOnce({
      session_id: 'session-test',
      text: 'Tương ớt Chin Su chai 1kg có giá tham khảo 62.200đ, thành phần gồm nước, đường, ớt tươi. Bạn xem trên nhãn sản phẩm nhé.',
      grounded_product_ids: [],
      state: 'BROWSING',
    })
    const wrapper = await startSpokenReply()
    const audio = wrapper.get('audio').element as HTMLAudioElement
    await vi.waitFor(() => expect(audio.onended).toBeTypeOf('function'))
    const mic = controllerStore.current
    vi.useFakeTimers()
    audio.onended?.call(audio, new Event('ended'))
    await vi.advanceTimersByTimeAsync(650)
    expect(mic.isActive).toBe(true)
    expect(mic.bargeInMode).toBe(false)
    vi.useRealTimers()

    // Short price-unit & tone-shifted loudspeaker echo snippets arriving within the strict post-speech window are dropped.
    apiMocks.transcribe.mockResolvedValueOnce({
      session_id: 'session-test',
      text: '62.200 đồng',
      language: 'vi',
      speech_detected: true,
    })
    mic.options.onAudio(new Blob(['tail price echo']))
    await vi.waitFor(() => expect(apiMocks.transcribe).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(wrapper.get('.voice-feedback strong').text()).toBe('Đang nghe'))
    expect(apiMocks.respond).toHaveBeenCalledTimes(1)

    // Genuine short customer command right after Mai finishes still submits normally.
    apiMocks.transcribe.mockResolvedValueOnce({
      session_id: 'session-test',
      text: 'Thêm vào giỏ',
      language: 'vi',
      speech_detected: true,
    })
    apiMocks.respond.mockResolvedValueOnce({
      session_id: 'session-test',
      text: 'Mình đã thêm sản phẩm vào giỏ hàng.',
      grounded_product_ids: [],
      state: 'BROWSING',
    })
    mic.options.onAudio(new Blob(['customer command']))
    await vi.waitFor(() => expect(apiMocks.respond).toHaveBeenCalledTimes(2))
    expect(apiMocks.respond.mock.calls[1][1]).toBe('Thêm vào giỏ')
    wrapper.unmount()
  })

  it('requires real non-echo STT words before interrupting Mai by default so laptop fan noise and self-echo cannot barge in', async () => {
    const { pause } = setupCloudReply()
    const wrapper = await startSpokenReply()
    const mic = controllerStore.current
    await vi.waitFor(() => expect(mic.bargeInMode).toBe(true))
    expect(mic.options.requireSttForBargeIn).toBe(true)

    // 1. Candidate burst from laptop cooling fan returns speech_detected=false -> Mai keeps speaking uninterrupted!
    pause.mockClear()
    apiMocks.transcribe.mockResolvedValueOnce({
      session_id: 'session-test',
      text: '',
      language: 'vi',
      speech_detected: false,
    })
    mic.options.onBargeInCandidateAudio(new Blob(['laptop fan noise']))
    await vi.waitFor(() => expect(apiMocks.transcribe).toHaveBeenCalledTimes(1))
    expect(pause).not.toHaveBeenCalled()
    expect(mic.bargeInMode).toBe(true)
    expect(apiMocks.respond).toHaveBeenCalledTimes(1)

    // 2. Candidate burst with Mai's own speaker echo -> still ignored, Mai keeps speaking!
    apiMocks.transcribe.mockResolvedValueOnce({
      session_id: 'session-test',
      text: 'Mình gợi ý chai nước này.',
      language: 'vi',
      speech_detected: true,
    })
    mic.options.onBargeInCandidateAudio(new Blob(['assistant echo']))
    await vi.waitFor(() => expect(apiMocks.transcribe).toHaveBeenCalledTimes(2))
    expect(pause).not.toHaveBeenCalled()
    expect(apiMocks.respond).toHaveBeenCalledTimes(1)

    // 3. Genuine human Vietnamese speech verified by STT -> interrupts Mai and answers immediately!
    apiMocks.transcribe.mockResolvedValueOnce({
      session_id: 'session-test',
      text: 'Cho mình hỏi giá sữa tươi',
      language: 'vi',
      speech_detected: true,
    })
    apiMocks.respond.mockResolvedValueOnce({
      session_id: 'session-test',
      text: 'Sữa tươi Vinamilk có giá 32.000đ.',
      grounded_product_ids: [],
      state: 'BROWSING',
    })
    mic.options.onBargeInCandidateAudio(new Blob(['real customer speech']))
    await vi.waitFor(() => expect(apiMocks.respond).toHaveBeenCalledTimes(2))
    expect(pause).toHaveBeenCalled()
    expect(apiMocks.respond.mock.calls[1][1]).toBe('Cho mình hỏi giá sữa tươi')
    wrapper.unmount()
  })
})
