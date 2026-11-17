import { afterEach, describe, expect, it, vi } from 'vitest'
import { respond } from './api'

afterEach(() => vi.unstubAllGlobals())

describe('assistant failures', () => {
  it.each([
    [502, { code: 'assistant_busy', message: 'internal detail' }, 'tạm hết lượt trả lời'],
    [502, { code: 'assistant_unavailable', message: 'internal detail' }, 'chưa hoàn tất câu trả lời'],
    [504, 'Consultation timed out', 'lâu hơn dự kiến'],
  ])('explains response failure %s without claiming the session is disconnected', async (status, detail, expected) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail }), { status: Number(status) },
    )))
    await expect(respond('session-id', 'Giá các loại thịt?')).rejects.toThrow(String(expected))
  })
})
