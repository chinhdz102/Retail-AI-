import { describe, expect, it } from 'vitest'

import { isLikelyAssistantEcho, isLikelyRepeatedSpeechTranscript } from './transcript-quality'

describe('speech transcript loop guard', () => {
  it('flags the single-word loop reproduced from a cough recording', () => {
    expect(isLikelyRepeatedSpeechTranscript('Cơm, cơm, cơm, cơm, cơm, cơm, cơm.')).toBe(true)
    expect(isLikelyRepeatedSpeechTranscript('Không, không, tôi muốn mua cơm.')).toBe(false)
  })
  it('flags a repeated multiword Whisper loop', () => {
    expect(isLikelyRepeatedSpeechTranscript(
      'Cảm ơn các bạn đã theo dõi và ủng thông, và hạn thấy nhiều thông, và hạn thấy nhiều thông.',
    )).toBe(true)
  })

  it('keeps natural repetition in a shopping request', () => {
    expect(isLikelyRepeatedSpeechTranscript('Mua một chai nước, mua thêm hai chai nước.'))
      .toBe(false)
  })

  it('keeps a normal retail goodbye', () => {
    expect(isLikelyRepeatedSpeechTranscript(
      'Cảm ơn bạn đã ghé cửa hàng, hẹn gặp lại bạn lần sau nhé.',
    )).toBe(false)
  })
})

describe('assistant loudspeaker echo guard', () => {
  const spoken = 'Coca-Cola Zero 330ml hiện có giá 13.000đ, bạn muốn thêm vào giỏ không?'

  it('rejects verbatim, 3-word, and partially misheard loudspeaker echo of Mai', () => {
    expect(isLikelyAssistantEcho('hiện có giá 13.000đ bạn muốn thêm vào giỏ', spoken)).toBe(true)
    expect(isLikelyAssistantEcho('bạn muốn thêm vào giỏ không', spoken)).toBe(true)
    expect(isLikelyAssistantEcho('có giá 13.000đ bạn muốn thêm giỏ', spoken)).toBe(true)
    expect(isLikelyAssistantEcho('hiện có giá', spoken)).toBe(true)
    expect(isLikelyAssistantEcho('giá 13.000đ bạn muốn xem đồ', spoken)).toBe(true)
  })

  it('keeps genuine short and long customer interruptions', () => {
    expect(isLikelyAssistantEcho('Thêm vào giỏ', spoken)).toBe(false)
    expect(isLikelyAssistantEcho('Khoan đã, lấy hai lon Coca Zero', spoken)).toBe(false)
    expect(isLikelyAssistantEcho('Có loại nào rẻ hơn không', spoken)).toBe(false)
    expect(isLikelyAssistantEcho('Thêm vào giỏ giúp mình nhé', spoken)).toBe(false)
    expect(isLikelyAssistantEcho('Thôi không cần đâu', spoken)).toBe(false)
  })
})
