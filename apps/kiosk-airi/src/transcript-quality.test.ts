import { describe, expect, it } from 'vitest'

import {
  classifyVietnameseTurnCompleteness,
  isLikelyAssistantEcho,
  isLikelyRepeatedSpeechTranscript,
} from './transcript-quality'

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

  it('catches diacritic-shifted, price-unit, and 2-word loud-speaker echoes in strict mode', () => {
    const detailed = 'Tương ớt Chin Su chai 1kg có giá tham khảo 62.200đ, thành phần gồm nước, đường, ớt tươi 110 g/kg và muối. Bạn kiểm tra trên nhãn sản phẩm nhé.'
    expect(isLikelyAssistantEcho('nhân sản phẩm nhẹ', detailed)).toBe(true)
    expect(isLikelyAssistantEcho('giả tham khảo 62.200 đồng', detailed)).toBe(true)
    expect(isLikelyAssistantEcho('tương ớt chin su chạy 1 ki lô gam', detailed)).toBe(true)
    expect(isLikelyAssistantEcho('62.200 đồng', detailed, { strict: true })).toBe(true)
    expect(isLikelyAssistantEcho('ớt tươi', detailed, { strict: true })).toBe(true)
    expect(isLikelyAssistantEcho('nhãn sản phẩm', detailed, { strict: true })).toBe(true)
    expect(isLikelyAssistantEcho('kiểm tra sản phẩm', detailed, { strict: true })).toBe(true)
    expect(isLikelyAssistantEcho('nhé', detailed, { strict: true })).toBe(true)

    // Genuine short customer replies and distinct requests stay untouched even in strict mode.
    expect(isLikelyAssistantEcho('Có', detailed, { strict: true })).toBe(false)
    expect(isLikelyAssistantEcho('Không', detailed, { strict: true })).toBe(false)
    expect(isLikelyAssistantEcho('Thanh toán', detailed, { strict: true })).toBe(false)
    expect(isLikelyAssistantEcho('Rẻ hơn', detailed, { strict: true })).toBe(false)
    expect(isLikelyAssistantEcho('Thêm vào giỏ', detailed, { strict: true })).toBe(false)
    expect(isLikelyAssistantEcho('Giá nước suối', 'Mình có giá nước ngọt ở quầy hai.', { strict: true })).toBe(false)
  })
})

describe('Vietnamese turn completeness classifier', () => {
  it('distinguishes complete retail turns from mid-sentence dangling pauses', () => {
    expect(classifyVietnameseTurnCompleteness('Giá chai này bao nhiêu?')).toBe('complete')
    expect(classifyVietnameseTurnCompleteness('Thêm vào giỏ')).toBe('complete')
    expect(classifyVietnameseTurnCompleteness('Có loại ít đường hơn không')).toBe('complete')
    expect(classifyVietnameseTurnCompleteness('Cho mình hỏi là')).toBe('incomplete')
    expect(classifyVietnameseTurnCompleteness('Tương ớt Chin Su chai')).toBe('incomplete')
    expect(classifyVietnameseTurnCompleteness('Ở đây có')).toBe('incomplete')
    expect(classifyVietnameseTurnCompleteness('Mình muốn mua sữa với cả')).toBe('incomplete')
    expect(classifyVietnameseTurnCompleteness('Tương ớt Chin Su 1kg')).toBe('ambiguous')
  })
})
