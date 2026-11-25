import { describe, expect, it } from 'vitest'
import { extractSpeakableChunks } from './speech-queue'

describe('extractSpeakableChunks', () => {
  it('preserves Vietnamese VND thousand separators and decimals', () => {
    const first = extractSpeakableChunks(
      'Dạ, Khăn mặt cotton Thanh Lam Home giá 29.000đ và đơn giá 32.222đ/100g. Bên dưới còn nhiều mẫu khác nhé!',
      false,
    )
    expect(first.chunks).toEqual([
      'Dạ, Khăn mặt cotton Thanh Lam Home giá 29.000đ và đơn giá 32.222đ/100g.',
    ])
    expect(first.remainder).toBe('Bên dưới còn nhiều mẫu khác nhé!')

    const flushed = extractSpeakableChunks(first.remainder, true)
    expect(flushed.chunks).toEqual(['Bên dưới còn nhiều mẫu khác nhé!'])
    expect(flushed.remainder).toBe('')
  })

  it('waits before splitting when a trailing period could be a VND separator in progress', () => {
    const midNumber = extractSpeakableChunks('Món này có giá tham khảo 29.', false)
    expect(midNumber.chunks).toEqual([])
    expect(midNumber.remainder).toBe('Món này có giá tham khảo 29.')

    const completedNumber = extractSpeakableChunks(`${midNumber.remainder}000đ ạ. Mình gợi ý thêm nhé!`, false)
    expect(completedNumber.chunks).toEqual(['Món này có giá tham khảo 29.000đ ạ.'])
  })

  it('merges very short opening exclamations with the following sentence', () => {
    const result = extractSpeakableChunks('Dạ. Sản phẩm này đang giảm giá 41% còn 29.000đ. Bạn xem thêm nhé!', false)
    expect(result.chunks).toEqual(['Dạ. Sản phẩm này đang giảm giá 41% còn 29.000đ.'])
    expect(result.remainder).toBe('Bạn xem thêm nhé!')
  })

  it('keeps commas and semicolons inside one natural sentence instead of halting mid-thought', () => {
    const mediumText =
      'Dạ, sữa tươi TH True Milk 1L giá 36.000đ; ngoài ra có Vinamilk không đường 1L giá 34.500đ'
    const result = extractSpeakableChunks(mediumText, false)
    expect(result.chunks).toEqual([])
    expect(result.remainder).toBe(mediumText)
  })

  it('splits marathon run-on comma clauses only when a single sentence exceeds 150 chars', () => {
    const longText =
      'Dạ với nhu cầu dùng hằng ngày cho cả gia đình thích chất liệu mềm thấm hút tốt, mẫu khăn mặt cotton Thanh Lam Home 90g đang giảm 41 phần trăm rất đáng cân nhắc cho giỏ đồ lần này'
    const result = extractSpeakableChunks(longText, false)
    expect(result.chunks.length).toBe(1)
    expect(result.chunks[0]).toBe('Dạ với nhu cầu dùng hằng ngày cho cả gia đình thích chất liệu mềm thấm hút tốt,')
    expect(result.remainder).toContain('mẫu khăn mặt cotton')
  })

  it('handles Chinese sentence delimiters', () => {
    const result = extractSpeakableChunks('这款毛巾参考价格为29000越南盾，性价比很高。下面还有其他同款可供选择。', true)
    expect(result.chunks).toEqual([
      '这款毛巾参考价格为29000越南盾，性价比很高。',
      '下面还有其他同款可供选择。',
    ])
  })
})
