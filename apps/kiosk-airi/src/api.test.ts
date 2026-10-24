import { afterEach, describe, expect, it, vi } from 'vitest'

import { prewarmSpeech, respond, transcribe } from './api'

function eventStreamResponse(body: string): Response {
  const bytes = new TextEncoder().encode(body)
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const byte of bytes) controller.enqueue(new Uint8Array([byte]))
      controller.close()
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

describe('agent response stream API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('decodes incremental UTF-8 SSE events and assembles the validated response', async () => {
    const fetchMock = vi.fn().mockResolvedValue(eventStreamResponse([
      'event: start\ndata: {"response":{"session_id":"session-1","model":"catalog","grounded_product_ids":[],"tools_used":["search_products"],"state":"BROWSING"}}\n\n',
      'event: delta\ndata: {"text":"Xin chào "}\n\n',
      'event: delta\ndata: {"text":"bạn!"}\n\n',
      'event: complete\ndata: {"response":{"session_id":"session-1","model":"catalog","grounded_product_ids":[],"tools_used":["search_products"],"state":"BROWSING"}}\n\n',
    ].join('')))
    vi.stubGlobal('fetch', fetchMock)
    const deltas: string[] = []

    const result = await respond(
      'session-1',
      'Xin chào',
      'vi',
      'ccf7a3d6-9ca5-4aec-a052-f3e17e32a845',
      (delta) => deltas.push(delta),
    )

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toContain('/agent/respond/stream')
    expect(fetchMock.mock.calls[0][1].headers.Accept).toBe('text/event-stream')
    expect(deltas).toEqual(['Xin chào ', 'bạn!'])
    expect(result).toMatchObject({
      session_id: 'session-1',
      text: 'Xin chào bạn!',
      model: 'catalog',
      tools_used: ['search_products'],
    })
  })

  it('does not accept a truncated stream without a complete event', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(eventStreamResponse(
      'event: start\ndata: {"response":{"session_id":"session-1","model":"catalog","grounded_product_ids":[],"state":"BROWSING"}}\n\n'
      + 'event: delta\ndata: {"text":"Chưa xong"}\n\n',
    )))

    await expect(respond(
      'session-1',
      'Giá sản phẩm?',
      'vi',
      'ccf7a3d6-9ca5-4aec-a052-f3e17e32a845',
      () => undefined,
    )).rejects.toThrow('The response stream ended before completion.')
  })

  it('rejects a mismatched session before showing any stream text', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(eventStreamResponse([
      'event: start\ndata: {"response":{"session_id":"session-other","model":"catalog","grounded_product_ids":[],"state":"BROWSING"}}\n\n',
      'event: delta\ndata: {"text":"Thông tin của phiên khác"}\n\n',
    ].join(''))))
    const deltas: string[] = []

    await expect(respond(
      'session-1',
      'Giá Coca Zero?',
      'vi',
      'ccf7a3d6-9ca5-4aec-a052-f3e17e32a845',
      (delta) => deltas.push(delta),
    )).rejects.toThrow('không thuộc phiên hội thoại hiện tại')

    expect(deltas).toEqual([])
  })

  it('rejects a mismatched session from the non-stream endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      session_id: 'session-other',
      text: 'Thông tin của phiên khác',
      model: 'catalog',
      grounded_product_ids: [],
      state: 'BROWSING',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))

    await expect(respond('session-1', 'Giá Coca Zero?')).rejects.toThrow(
      'không thuộc phiên hội thoại hiện tại',
    )
  })

  it('rejects cart data attached to a different session', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      session_id: 'session-1',
      text: 'Coca Zero có giá 13.000đ.',
      model: 'catalog',
      grounded_product_ids: [],
      state: 'BROWSING',
      cart: {
        session_id: 'session-other',
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))

    await expect(respond('session-1', 'Giá Coca Zero?')).rejects.toThrow(
      'không thuộc phiên hội thoại hiện tại',
    )
  })

  it('aborts an in-flight stream when its owning session is cancelled', async () => {
    const fetchMock = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()
    const response = respond('session-1', 'Giá Coca Zero?', 'vi', undefined, () => undefined, controller.signal)
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    controller.abort()

    await expect(response).rejects.toMatchObject({ name: 'AbortError' })
  })
})

describe('audio transcription API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sends an audio filename matching the actual recording format', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      session_id: 'session-1',
      text: 'Xin chào',
      language: 'vi',
      model: 'whisper-large-v3',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await transcribe('session-1', new Blob(['audio'], { type: 'audio/mp4' }), 'vi')

    const form = fetchMock.mock.calls[0][1]?.body as FormData
    const file = form.get('file') as File
    expect(file.name).toBe('voice.m4a')
    expect(file.type).toBe('audio/mp4')
  })
})

describe('speech prewarm API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('preconnects the selected session and language without sending speech text', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      warmed: true,
      provider: 'azure-speech',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(prewarmSpeech('session-1', 'vi')).resolves.toEqual({
      warmed: true,
      provider: 'azure-speech',
    })

    expect(fetchMock.mock.calls[0][0]).toContain('/audio/synthesize/prewarm')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
      session_id: 'session-1',
      language: 'vi',
    })
  })
})
