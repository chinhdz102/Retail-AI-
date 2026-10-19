import { afterEach, describe, expect, it, vi } from 'vitest'

import { respond } from './api'

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
      'event: delta\ndata: {"text":"Chưa xong"}\n\n',
    )))

    await expect(respond(
      'session-1',
      'Giá sản phẩm?',
      'vi',
      'ccf7a3d6-9ca5-4aec-a052-f3e17e32a845',
      () => undefined,
    )).rejects.toThrow('The response stream ended before completion.')
  })
})
