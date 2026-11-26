import { mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ transcribe: vi.fn(), controller: null as any }))
vi.mock('./api', () => ({
  createSession: vi.fn().mockResolvedValue({ session_id: 'debug-session' }),
  createRequestId: vi.fn(() => 'id'), closeSession: vi.fn().mockResolvedValue({}),
  getCart: vi.fn().mockResolvedValue(null),
  getManagerNotifierStatus: vi.fn().mockResolvedValue({ enabled: false, primary_channel: null }),
  prewarmSpeech: vi.fn().mockResolvedValue({}),
  transcribe: mocks.transcribe, respond: vi.fn(), createSynthesisTicket: vi.fn(),
  addCartItem: vi.fn(), setCartItemQuantity: vi.fn(), prepareCheckout: vi.fn(),
  cancelCheckout: vi.fn(), confirmCheckout: vi.fn(), simulateSandboxPayment: vi.fn(),
  submitAssistedOrder: vi.fn(), listAssistedOrders: vi.fn().mockResolvedValue([]),
  getPayOSStatus: vi.fn().mockResolvedValue({ enabled: false, provider: 'payos', store_name: 'Thanh Lam Mart' }),
  syncPayOSPayment: vi.fn(), cancelPayOSPayment: vi.fn(),
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
vi.mock('./voice-controller', () => ({ VoiceConversationController: class {
  options: any
  constructor(options: any) { this.options = options; mocks.controller = this }
  setLocale() {}
  start() { this.options.onState({ mode: 'recorder', phase: 'listening', permission: 'granted' }); return Promise.resolve() }
  pause() {}
  stop() {}
} }))
import App from './App.vue'

afterEach(() => { window.history.replaceState({}, '', '/'); vi.restoreAllMocks(); mocks.transcribe.mockReset() })

it('hides diagnostics from the default customer view', async () => {
  const wrapper = mount(App)
  expect(wrapper.find('[aria-label="Local voice diagnostics"]').exists()).toBe(false)
  wrapper.unmount()
})

it('requires opt-in and cannot restore a cleared recording when STT finishes late', async () => {
  window.history.replaceState({}, '', '/?voiceDebug=1')
  let resolve!: (value: any) => void
  mocks.transcribe.mockReturnValue(new Promise((done) => { resolve = done }))
  let index = 0
  const create = vi.spyOn(URL, 'createObjectURL').mockImplementation(() => `blob:debug-${++index}`)
  const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  const wrapper = mount(App)
  expect((wrapper.get('input[type="checkbox"]').element as HTMLInputElement).checked).toBe(false)
  await wrapper.get('input[type="checkbox"]').setValue(true)
  await vi.waitFor(() => expect(wrapper.find('button[aria-label="Bắt đầu nói"]').exists()).toBe(true))
  await wrapper.get('button[aria-label="Bắt đầu nói"]').trigger('click')
  mocks.controller.options.onCapture({ source: 'silero-v6', settings: { sampleRate: 48000 } })
  const audio = new Blob(['the exact upload'], { type: 'audio/wav' })
  mocks.controller.options.onAudio(audio)
  await vi.waitFor(() => expect(mocks.transcribe).toHaveBeenCalledWith('debug-session', audio, 'vi', expect.any(AbortSignal), true))
  expect(create.mock.calls[0]?.[0]).toBe(audio)
  expect(wrapper.find('[aria-label="Exact uploaded recording"]').exists()).toBe(true)
  await wrapper.get('input[type="checkbox"]').setValue(false)
  expect(revoke).toHaveBeenCalledTimes(2)
  const calls = create.mock.calls.length
  resolve({ session_id: 'debug-session', text: '', speech_detected: false, language: 'vi', model: 'large-v3' })
  await vi.waitFor(() => expect(wrapper.find('[aria-label="Exact uploaded recording"]').exists()).toBe(false))
  expect(create).toHaveBeenCalledTimes(calls)
  wrapper.unmount()
})
