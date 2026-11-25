function tokenizeVietnameseText(text: string): string[] {
  return text.toLocaleLowerCase('vi').match(/[\p{L}\p{N}]+/gu) ?? []
}

/** Detect long repeated-phrase loops sometimes hallucinated by speech recognition. */
export function isLikelyRepeatedSpeechTranscript(text: string): boolean {
  const tokens = tokenizeVietnameseText(text)
  if (tokens.length >= 6 && new Set(tokens).size === 1) return true
  if (tokens.length < 8) return false

  for (let size = Math.min(10, Math.floor(tokens.length / 2)); size >= 4; size -= 1) {
    const occurrences = new Map<string, number[]>()
    for (let start = 0; start <= tokens.length - size; start += 1) {
      const key = tokens.slice(start, start + size).join('\u0000')
      const starts = occurrences.get(key) ?? []
      starts.push(start)
      occurrences.set(key, starts)
    }

    for (const starts of occurrences.values()) {
      let count = 0
      let nextAllowedStart = -1
      for (const start of starts) {
        if (start < nextAllowedStart) continue
        count += 1
        nextAllowedStart = start + size
      }
      if (count >= 2 && (count * size) / tokens.length >= 0.35) return true
    }
  }

  return false
}

const LEGITIMATE_SHORT_CUSTOMER_PHRASES = new Set([
  'thêm vào giỏ',
  'bỏ ra giỏ',
  'bỏ ra ngoài',
  'lấy cái này',
  'chọn cái này',
  'chọn món này',
  'có loại nào',
  'có loại khác',
  'có loại khác không',
  'loại nào rẻ',
  'rẻ hơn không',
  'khoan đã',
  'dừng lại',
  'thôi không cần',
  'không cần đâu',
  'cảm ơn nhé',
  'cảm ơn bạn',
  'tạm biệt nhé',
])

/**
 * Detect when full-duplex barge-in or post-speech reverberation accidentally
 * captures Mai's own loudspeaker TTS instead of a real customer utterance.
 * Whitelists common short customer commands while rejecting verbatim 3-word
 * echo snippets and partially misheard >= 4-word assistant bleedthrough.
 */
export function isLikelyAssistantEcho(
  transcript: string,
  spokenText: string | null | undefined,
): boolean {
  if (!spokenText) return false
  const transcriptTokens = tokenizeVietnameseText(transcript)
  const spokenTokens = tokenizeVietnameseText(spokenText)
  if (transcriptTokens.length < 3 || spokenTokens.length < 3) return false

  const transcriptPhrase = transcriptTokens.join(' ')
  if (LEGITIMATE_SHORT_CUSTOMER_PHRASES.has(transcriptPhrase)) return false

  const spokenPhrase = spokenTokens.join(' ')
  if (spokenPhrase.includes(transcriptPhrase)) return true

  if (transcriptTokens.length < 4) return false

  const spokenSet = new Set(spokenTokens)
  let overlap = 0
  for (const token of transcriptTokens) {
    if (spokenSet.has(token)) overlap += 1
  }
  const overlapRatio = overlap / transcriptTokens.length
  if (overlapRatio < 0.55) return false

  const spokenBigrams = new Set<string>()
  for (let i = 0; i < spokenTokens.length - 1; i += 1) {
    spokenBigrams.add(`${spokenTokens[i]}\u0000${spokenTokens[i + 1]}`)
  }
  let sharedBigrams = 0
  for (let i = 0; i < transcriptTokens.length - 1; i += 1) {
    if (spokenBigrams.has(`${transcriptTokens[i]}\u0000${transcriptTokens[i + 1]}`)) {
      sharedBigrams += 1
    }
  }
  return sharedBigrams >= 2 || (overlapRatio >= 0.65 && sharedBigrams >= 1)
}
