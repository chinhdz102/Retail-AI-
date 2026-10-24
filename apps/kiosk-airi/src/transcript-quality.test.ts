import { describe, expect, it } from 'vitest'

import { isLikelyRepeatedSpeechTranscript } from './transcript-quality'

describe('speech transcript loop guard', () => {
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
