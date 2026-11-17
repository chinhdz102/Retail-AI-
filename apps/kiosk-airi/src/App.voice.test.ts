import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  createSession: vi.fn(), closeSession: vi.fn(), createRequestId: vi.fn(() => 'turn'),
  respond: vi.fn(), transcribe: vi.fn(), prepareVoice: vi.fn(), createSynthesisTicket: vi.fn(), getCart: vi.fn(),
  addCartItem: vi.fn(), setCartItemQuantity: vi.fn(), prepareCheckout: vi.fn(),
  confirmCheckout: vi.fn(), cancelCheckout: vi.fn(), simulateSandboxPayment: vi.fn(),
}))
const store = vi.hoisted(() => ({ controller: null as any }))
vi.mock('./api', () => api)
vi.mock('./voice-controller', () => ({
  VoiceConversationController: class {
    options: any
    startCalls = 0
    isActive = false
    constructor(options: any) { this.options = options; store.controller = this }
    setInputDevice = vi.fn()
    async start() {
      this.startCalls++
      this.isActive = true
      this.options.onState({ mode: 'recorder', phase: 'listening', permission: 'granted' })
    }
    pause() {
      this.isActive = false
      this.options.onState({ mode: 'idle', phase: 'idle', permission: 'granted' })
    }
    stop() { this.pause() }
    stopCapture() { this.pause() }
  },
}))
import App from './App.vue'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
beforeEach(() => {
  vi.useFakeTimers()
  vi.resetAllMocks()
  api.createSession.mockResolvedValue({
    session_id: 'session', expires_at: new Date(Date.now() + 1800000).toISOString(),
  })
  api.getCart.mockResolvedValue(null)
  api.prepareVoice.mockResolvedValue({ ready: true })
  api.closeSession.mockResolvedValue({})
  api.createRequestId.mockReturnValue('turn')
  api.transcribe.mockResolvedValue({ text: 'Sữa giá bao nhiêu?', speech_detected: true })
  api.respond.mockResolvedValue({ text: 'Giá sữa là 10.000đ.', state: 'BROWSING' })
  api.createSynthesisTicket.mockResolvedValue('http://localhost/audio/one-use')
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
  store.controller = null
  window.localStorage.removeItem('retail-ai-microphone-device')
})
afterEach(() => {
  vi.useRealTimers(); vi.restoreAllMocks()
  vi.unstubAllGlobals()
  Reflect.deleteProperty(navigator, 'mediaDevices')
  window.localStorage.removeItem('retail-ai-microphone-device')
})
async function start() {
  const wrapper = mount(App, { global: { stubs: { SkyAvatar: true } } })
  await flushPromises()
  await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
  await flushPromises()
  return wrapper
}
function audio() { store.controller.options.onAudio(new Blob(['wav'], { type: 'audio/wav' })) }
function speechStart() {
  store.controller.options.onSpeechStart?.()
  store.controller.options.onState({ mode: 'recorder', phase: 'recording', permission: 'granted' })
}

describe('Whisper voice integration and TTS isolation', () => {
  it('does not submit a preview until the final endpoint, then reuses its ASR result once', async () => {
    const w = await start()
    store.controller.options.onAudioPreview(new Blob(['preview']), 101)
    await flushPromises()
    expect(api.transcribe).toHaveBeenCalledTimes(1)
    expect(api.respond).not.toHaveBeenCalled()
    expect(w.findAll('.message-user')).toHaveLength(0)
    store.controller.options.onAudio(new Blob(['full']), 101)
    await flushPromises()
    expect(api.transcribe).toHaveBeenCalledTimes(1)
    expect(api.respond).toHaveBeenCalledTimes(1)
    w.unmount()
  })

  it('abandons a preview when speech resumes and sends the complete corrected audio', async () => {
    const old = deferred<any>()
    api.transcribe.mockReturnValueOnce(old.promise)
    const w = await start()
    store.controller.options.onAudioPreview(new Blob(['prefix']), 102)
    const signal = api.transcribe.mock.calls[0]![3] as AbortSignal
    store.controller.options.onAudioPreviewInvalidated(102)
    expect(signal.aborted).toBe(true)
    const full = new Blob(['prefix+correction'])
    store.controller.options.onAudio(full)
    old.resolve({ text: 'Câu chưa đủ', speech_detected: true })
    await flushPromises()
    expect(api.transcribe.mock.calls[1]![1]).toBe(full)
    expect(api.respond).toHaveBeenCalledExactlyOnceWith('session', 'Sữa giá bao nhiêu?', 'vi', 'turn')
    w.unmount()
  })

  it.each(['error', 'rejected'])('retries the original full recording after a %s preview', async failure => {
    if (failure === 'error') api.transcribe.mockRejectedValueOnce(new Error('network'))
    else api.transcribe.mockResolvedValueOnce({ text: '', speech_detected: false })
    const w = await start()
    store.controller.options.onAudioPreview(new Blob(['preview']), 103)
    await flushPromises()
    const full = new Blob(['original full WAV'])
    store.controller.options.onAudio(full, 103)
    await flushPromises()
    expect(api.transcribe).toHaveBeenCalledTimes(2)
    expect(api.transcribe.mock.calls[1]![1]).toBe(full)
    expect(api.respond).toHaveBeenCalledTimes(1)
    w.unmount()
  })

  it('cancels a preview on Stop without committing its delayed result', async () => {
    const old = deferred<any>()
    api.transcribe.mockReturnValueOnce(old.promise)
    const w = await start()
    store.controller.options.onAudioPreview(new Blob(['preview']), 104)
    const signal = api.transcribe.mock.calls[0]![3] as AbortSignal
    await w.get('button[aria-label="Dừng nghe"]').trigger('click')
    expect(signal.aborted).toBe(true)
    old.resolve({ text: 'Lượt đã hủy', speech_detected: true })
    await flushPromises()
    expect(api.respond).not.toHaveBeenCalled()
    w.unmount()
  })

  it.each(['Em mua thịt về ăn lẩu.', 'Em mua thịt về ăn lấu.', 'Em cần nước lau sàng.'])(
    'preserves tones from the accepted API transcript in display and chat: %s', async text => {
      api.transcribe.mockResolvedValue({ text, speech_detected: true })
      const w = await start()
      audio()
      await flushPromises()
      expect(api.respond).toHaveBeenCalledWith('session', text, 'vi', 'turn')
      expect(w.get('.message-user').text()).toContain(text)
      w.unmount()
    },
  )

  it('keeps an explicit mic choice when an older device scan resolves later', async () => {
    const discovery = deferred<any[]>()
    const devices = [
      { deviceId: 'physical-a', groupId: 'a', kind: 'audioinput', label: 'Microphone A' },
      { deviceId: 'physical-b', groupId: 'b', kind: 'audioinput', label: 'Microphone B' },
    ]
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
      enumerateDevices: vi.fn().mockResolvedValueOnce(devices)
        .mockReturnValueOnce(discovery.promise).mockResolvedValue(devices),
      addEventListener: vi.fn(), removeEventListener: vi.fn(),
    } })
    const w = await start()
    await w.get('select[aria-label="Chọn micro đầu vào"]').setValue('physical-b')
    await flushPromises()
    discovery.resolve([devices[0]])
    await flushPromises()
    expect((w.get('select[aria-label="Chọn micro đầu vào"]').element as HTMLSelectElement).value).toBe('physical-b')
    expect(store.controller.setInputDevice).toHaveBeenLastCalledWith('physical-b')
    expect(store.controller.startCalls).toBe(2)
    w.unmount()
  })

  it('does not cut an utterance when device discovery finishes after speech has started', async () => {
    const discovery = deferred<any[]>()
    const enumerateDevices = vi.fn()
      .mockResolvedValueOnce([])
      .mockReturnValue(discovery.promise)
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
      enumerateDevices, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    } })
    const w = await start()
    store.controller.options.onInputDevice('Default - Microphone A')
    store.controller.options.onState({ mode: 'recorder', phase: 'recording', permission: 'granted' })
    discovery.resolve([{ deviceId: 'physical-b', groupId: 'b', kind: 'audioinput', label: 'Microphone B' }])
    await flushPromises()
    expect(store.controller.startCalls).toBe(1)
    expect(store.controller.isActive).toBe(true)
    audio(); await flushPromises()
    expect(api.transcribe).toHaveBeenCalledTimes(1)
    expect(api.respond).toHaveBeenCalledWith('session', 'Sữa giá bao nhiêu?', 'vi', 'turn')
    w.unmount()
  })

  it('keeps the same warm listener across natural TTS completion', async () => {
    const w = await start()
    audio()
    await flushPromises()
    expect(api.transcribe).toHaveBeenCalledWith('session', expect.any(Blob), 'vi', expect.any(AbortSignal))
    expect(api.respond).toHaveBeenCalledWith('session', 'Sữa giá bao nhiêu?', 'vi', 'turn')
    expect(store.controller.isActive).toBe(true)
    expect(w.findAll('.message-user')).toHaveLength(1)
    const element = w.get('audio').element as HTMLAudioElement
    element.dispatchEvent(new Event('ended'))
    expect(store.controller.startCalls).toBe(2)
    await vi.advanceTimersByTimeAsync(399)
    expect(store.controller.startCalls).toBe(2)
    await vi.advanceTimersByTimeAsync(1)
    expect(store.controller.startCalls).toBe(2)
    expect(store.controller.isActive).toBe(true)
    expect(w.text()).toContain('Đang nghe')
    w.unmount()
  })

  it.each([
    { text: '', speech_detected: false },
    { text: 'Đăng ký kênh', speech_detected: false },
    { text: '', speech_detected: true },
  ])('explains a missed utterance and resumes without submitting rejected text: %j', async result => {
    api.transcribe.mockResolvedValue(result)
    const w = await start()
    audio()
    await flushPromises()
    expect(api.respond).not.toHaveBeenCalled()
    expect(w.findAll('.message-user')).toHaveLength(0)
    expect(w.find('.chat-error').exists()).toBe(false)
    expect(store.controller.startCalls).toBe(2)
    expect(w.text()).toContain('Mình chưa nhận rõ câu vừa rồi')
  })

  it('ignores duplicate audio during transcription and delayed text after Stop', async () => {
    const pending = deferred<any>()
    api.transcribe.mockReturnValue(pending.promise)
    const w = await start()
    audio(); audio()
    await flushPromises()
    expect(api.transcribe).toHaveBeenCalledTimes(1)
    const signal = api.transcribe.mock.calls[0]?.[3] as AbortSignal
    const button = w.get('button[aria-label="Dừng nghe"]')
    expect(button.attributes('disabled')).toBeUndefined()
    await button.trigger('click')
    expect(signal.aborted).toBe(true)
    pending.resolve({ text: 'Đăng ký kênh', speech_detected: true })
    await flushPromises()
    expect(api.respond).not.toHaveBeenCalled()
    expect(store.controller.startCalls).toBe(1)
  })

  it('ignores response from a stopped turn even after mic is restarted', async () => {
    const pending = deferred<any>()
    api.transcribe.mockReturnValue(pending.promise)
    const w = await start()
    audio(); await flushPromises()
    await w.get('button[aria-label="Dừng nghe"]').trigger('click')
    await w.get('button[aria-label="Bắt đầu nói"]').trigger('click')
    pending.resolve({ text: 'Câu cũ', speech_detected: true })
    await flushPromises()
    expect(api.respond).not.toHaveBeenCalled()
    expect(store.controller.isActive).toBe(true)
  })

  it('stops TTS at confirmed onset before the full next utterance is uploaded', async () => {
    const pending = deferred<any>()
    api.respond.mockReturnValue(pending.promise)
    const w = await start()
    audio(); await flushPromises()
    audio()
    pending.resolve({ text: 'Trả lời', state: 'BROWSING' })
    await flushPromises()
    const element = w.get('audio').element as HTMLAudioElement
    const oldEnded = element.onended!
    const pause = vi.mocked(HTMLMediaElement.prototype.pause)
    const previousPauses = pause.mock.calls.length
    speechStart()
    expect(pause.mock.calls.length).toBeGreaterThan(previousPauses)
    expect(element.hasAttribute('src')).toBe(false)
    expect(HTMLMediaElement.prototype.load).toHaveBeenCalled()
    expect(store.controller.isActive).toBe(true)
    expect(api.transcribe).toHaveBeenCalledTimes(1)
    oldEnded.call(element, new Event('ended'))
    audio()
    audio()
    expect(api.transcribe).toHaveBeenCalledTimes(2)
    w.unmount()
  })

  it('does not interrupt on an unconfirmed level spike during TTS', async () => {
    const w = await start()
    audio(); await flushPromises()
    const element = w.get('audio').element as HTMLAudioElement
    store.controller.options.onLevel(1)
    store.controller.options.onNotice('Âm thanh quá ngắn')
    audio(); await flushPromises()
    expect(element.hasAttribute('src')).toBe(true)
    expect(api.transcribe).toHaveBeenCalledTimes(1)
    expect(w.text()).toContain('Đang trả lời')
    w.unmount()
  })

  it('cancels pending TTS when the user starts before the audio ticket arrives', async () => {
    const ticket = deferred<string>()
    api.createSynthesisTicket.mockReturnValueOnce(ticket.promise)
    const w = await start()
    audio(); await flushPromises()
    expect(store.controller.isActive).toBe(true)
    const signal = api.createSynthesisTicket.mock.calls[0]?.[3] as AbortSignal
    speechStart()
    expect(signal.aborted).toBe(true)
    ticket.resolve('http://localhost/old-audio')
    await flushPromises()
    expect(w.get('audio').attributes('src')).toBeUndefined()
    expect(api.transcribe).toHaveBeenCalledTimes(1)
    audio(); await flushPromises()
    expect(api.transcribe).toHaveBeenCalledTimes(2)
    w.unmount()
  })

  it('stops playback if the interrupt microphone fails during TTS', async () => {
    const w = await start()
    audio(); await flushPromises()
    const element = w.get('audio').element as HTMLAudioElement
    expect(element.hasAttribute('src')).toBe(true)
    store.controller.options.onError({ kind: 'runtime', message: 'Mất kết nối micro' })
    await flushPromises()
    expect(element.hasAttribute('src')).toBe(false)
    expect(w.get('.chat-error').text()).toContain('Mất kết nối micro')
    expect(w.get('button[aria-label="Bắt đầu nói"]')).toBeTruthy()
    w.unmount()
  })

  it('also interrupts the browser voice fallback without closing the new capture', async () => {
    const synthesis = {
      cancel: vi.fn(), resume: vi.fn(), getVoices: vi.fn(() => []), speak: vi.fn(),
    }
    vi.stubGlobal('speechSynthesis', synthesis)
    vi.stubGlobal('SpeechSynthesisUtterance', class {
      text: string
      constructor(text: string) { this.text = text }
    })
    api.createSynthesisTicket.mockRejectedValueOnce(new Error('Cloud TTS unavailable'))
    const w = await start()
    audio(); await flushPromises()
    expect(synthesis.speak).toHaveBeenCalledTimes(1)
    const utterance = synthesis.speak.mock.calls[0]![0] as any
    const cancellations = synthesis.cancel.mock.calls.length
    speechStart()
    expect(synthesis.cancel.mock.calls.length).toBe(cancellations + 1)
    utterance.onend()
    expect(store.controller.isActive).toBe(true)
    expect(api.transcribe).toHaveBeenCalledTimes(1)
    audio(); await flushPromises()
    expect(api.transcribe).toHaveBeenCalledTimes(2)
    w.unmount()
  })

  it('does not speak or reopen the mic for a chat response arriving after Stop', async () => {
    const pending = deferred<any>()
    api.respond.mockReturnValue(pending.promise)
    const w = await start()
    audio(); await flushPromises()
    await w.get('button[aria-label="Dừng nghe"]').trigger('click')
    pending.resolve({ text: 'Câu trả lời cũ', state: 'BROWSING' })
    await flushPromises()
    expect(api.createSynthesisTicket).not.toHaveBeenCalled()
    expect(w.text()).not.toContain('Câu trả lời cũ')
  })

  it('cancels a pending speaker-tail restart when voice mode is stopped', async () => {
    const w = await start()
    audio(); await flushPromises()
    w.get('audio').element.dispatchEvent(new Event('ended'))
    await w.get('button[aria-label="Dừng nghe"]').trigger('click')
    await vi.advanceTimersByTimeAsync(500)
    expect(store.controller.startCalls).toBe(2)
  })

  it('allows Stop Sky without restarting a warm microphone', async () => {
    const w = await start()
    audio(); await flushPromises()
    await w.get('button[aria-label="Dừng Sky nói"]').trigger('click')
    expect(store.controller.startCalls).toBe(2)
    await vi.advanceTimersByTimeAsync(400)
    expect(store.controller.startCalls).toBe(2)
    expect(store.controller.isActive).toBe(true)
  })

  it('aborts and discards recognition after unmount', async () => {
    const pending = deferred<any>()
    api.transcribe.mockReturnValue(pending.promise)
    const w = await start()
    audio(); await flushPromises()
    const signal = api.transcribe.mock.calls[0]?.[3] as AbortSignal
    w.unmount()
    pending.resolve({ text: 'Câu cũ', speech_detected: true })
    await flushPromises()
    expect(signal.aborted).toBe(true)
    expect(api.respond).not.toHaveBeenCalled()
  })

  it('resumes listening with a visible provider error', async () => {
    api.transcribe.mockRejectedValue(new Error('Nhận diện tạm thời không khả dụng'))
    const w = await start()
    audio(); await flushPromises()
    expect(store.controller.startCalls).toBe(2)
    expect(w.get('.chat-error').text()).toContain('Nhận diện tạm thời')
    expect(api.respond).not.toHaveBeenCalled()
  })

  it('does not replay old microphone audio into a renewed session', async () => {
    api.transcribe.mockRejectedValue(Object.assign(new Error('Session expired'), { status: 410 }))
    const w = await start()
    audio(); await flushPromises()
    expect(api.createSession).toHaveBeenCalledTimes(2)
    expect(api.transcribe).toHaveBeenCalledTimes(1)
    expect(api.respond).not.toHaveBeenCalled()
    expect(w.findAll('.message-user')).toHaveLength(0)
  })

  it('retires old playback completion when typing a new question during TTS', async () => {
    const w = await start()
    audio(); await flushPromises()
    const oldEnded = (w.get('audio').element as HTMLAudioElement).onended!
    const pending = deferred<any>()
    api.respond.mockReturnValue(pending.promise)
    await w.get('textarea').setValue('Câu hỏi tiếp theo')
    await w.get('.composer').trigger('submit')
    oldEnded.call(w.get('audio').element, new Event('ended'))
    await vi.advanceTimersByTimeAsync(500)
    expect(store.controller.startCalls).toBe(2)
    expect(w.text()).toContain('Đang xử lý')
    pending.resolve({ text: 'Trả lời mới', state: 'BROWSING' })
    await flushPromises()
  })
})
