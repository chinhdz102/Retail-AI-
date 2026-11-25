import { describe, expect, it } from 'vitest'
import {
  StreamingSttSession,
  buildStreamingSttUrl,
  float32ToPcm16Buffer,
} from './voice-stream-stt'

class FakeWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3

  readonly url: string
  binaryType = 'blob'
  readyState = FakeWebSocket.OPEN
  sent: Array<string | ArrayBuffer> = []
  onmessage: ((event: { data: unknown }) => void) | null = null
  onerror: ((event: unknown) => void) | null = null
  onclose: ((event: unknown) => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.last = this
  }

  send(data: string | ArrayBuffer) {
    this.sent.push(data)
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED
  }

  emit(payload: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(payload) })
  }

  static last: FakeWebSocket | null = null
}

describe('voice-stream-stt', () => {
  it('derives ws URL and converts Float32 samples into signed PCM16', () => {
    expect(buildStreamingSttUrl('abc-123', 'vi')).toMatch(/^ws:\/\/.+\/audio\/transcribe\/stream\?session_id=abc-123&language=vi$/)
    const pcm = new Int16Array(float32ToPcm16Buffer(new Float32Array([-1, 0, 0.5, 1])))
    expect(Array.from(pcm)).toEqual([-32768, 0, 16384, 32767])
  })

  it('buffers pre-roll frames until ready, emits partials, and resolves final transcript', async () => {
    const partials: string[] = []
    const session = new StreamingSttSession({
      sessionId: 'sess-1',
      language: 'vi',
      onPartial: (text) => partials.push(text),
      WebSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    })
    const ws = FakeWebSocket.last!
    session.pushFrame(new Float32Array([0.1, -0.1, 0.2]))
    expect(ws.sent.length).toBe(0)

    ws.emit({ type: 'ready', locale: 'vi-VN' })
    expect(ws.sent.length).toBe(1)
    expect(ws.sent[0]).toBeInstanceOf(ArrayBuffer)

    session.pushFrame(new Float32Array([0.3, -0.3]))
    expect(ws.sent.length).toBe(2)

    ws.emit({ type: 'partial', text: 'Cho mình xem khăn mặt' })
    expect(partials).toEqual(['Cho mình xem khăn mặt'])

    const finalPromise = session.finish(1_000)
    expect(ws.sent[ws.sent.length - 1]).toBe(JSON.stringify({ type: 'end' }))

    ws.emit({
      type: 'final',
      session_id: 'sess-1',
      text: 'Cho mình xem khăn mặt cotton Thanh Lam Home',
      model: 'azure-ws-stream:vi-VN',
      speech_detected: true,
    })

    const result = await finalPromise
    expect(result).toEqual({
      session_id: 'sess-1',
      language: 'vi',
      text: 'Cho mình xem khăn mặt cotton Thanh Lam Home',
      model: 'azure-ws-stream:vi-VN',
      speech_detected: true,
    })
  })

  it('resolves null cleanly when backend requests fallback', async () => {
    const session = new StreamingSttSession({
      sessionId: 'sess-2',
      language: 'vi',
      WebSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    })
    const ws = FakeWebSocket.last!
    const promise = session.finish(1_000)
    ws.emit({ type: 'fallback', reason: 'azure_stt_disabled' })
    expect(await promise).toBeNull()
  })

  it('tracks latestPartialText and resolves Smart Turn check_turn responses', async () => {
    const session = new StreamingSttSession({
      sessionId: 'sess-3',
      language: 'vi',
      WebSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    })
    const ws = FakeWebSocket.last!
    ws.emit({ type: 'ready', locale: 'vi-VN' })
    ws.emit({ type: 'partial', text: 'Cho mình hỏi là' })
    expect(session.latestPartialText).toBe('Cho mình hỏi là')

    const turnPromise = session.checkTurn(500)
    expect(ws.sent[ws.sent.length - 1]).toBe(JSON.stringify({ type: 'check_turn' }))
    ws.emit({
      type: 'turn_status',
      complete: false,
      probability: 0.12,
      hold_ms: 1050,
      reason: 'smart_turn_incomplete',
    })
    expect(await turnPromise).toEqual({
      complete: false,
      probability: 0.12,
      holdMs: 1050,
      reason: 'smart_turn_incomplete',
    })
    session.cancel()
  })
})
