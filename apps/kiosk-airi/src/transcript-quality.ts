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
  'có',
  'không',
  'ok',
  'được',
  'vâng',
  'dạ',
  'rồi',
  'xong',
  'tiếp',
  'dừng',
  'thôi',
  'khoan',
  'lấy',
  'mua',
  'hủy',
  'đổi',
  'thêm',
  'bỏ',
  'xóa',
  'giỏ hàng',
  'thanh toán',
  'tính tiền',
  'gọi quản lý',
  'gặp quản lý',
  'giá bao nhiêu',
  'ở kệ nào',
  'kệ nào',
  'loại nào',
  'có không',
  'rẻ hơn',
  'ít đường',
  'không đường',
  'cảm ơn',
  'tạm biệt',
  'xin chào',
  'chào mai',
  'đúng rồi',
  'không phải',
  'lấy một',
  'lấy hai',
  'thêm một',
  'thêm hai',
  'một cái',
  'hai cái',
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

const CUSTOMER_INTERRUPTION_PREFIXES = [
  'khoan da',
  'dung lai',
  'cho minh',
  'cho toi',
  'lay cho',
  'tim giup',
  'xem giup',
  'them vao gio',
  'bo ra gio',
  'co loai',
  'gia bao nhieu',
  'o ke nao',
  'thoi khong',
  'khong can',
]

const STRICT_SINGLE_TOKEN_ECHO_FRAGMENTS = new Set([
  'nhe',
  'nha',
  'dong',
  'nghin',
  'tram',
  'trieu',
  'gam',
  'ml',
  'kg',
  'cua',
  'voi',
  'hoac',
  'nhung',
  'ban',
  'minh',
  'nhan',
  'pham',
])

export interface AssistantEchoOptions {
  /**
   * Stricter guard right after barge-in or within a few seconds of Mai
   * speaking, catching 2-word and slightly distorted 3-word loudspeaker echoes
   * while preserving whitelisted customer commands.
   */
  strict?: boolean
}

function stripVietnameseDiacritics(text: string): string {
  return text
    .toLocaleLowerCase('vi')
    .replace(/đ/g, 'd')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
}

/**
 * Normalize written retail prices/units and strip Vietnamese diacritics so
 * loudspeaker-distorted STT (tone flips, "62.200đ" vs "62.200 đồng",
 * "1kg" vs "1 ki lô gam") still aligns with Mai's spoken text.
 */
function normalizeRetailEchoTokens(text: string): string[] {
  const prepared = stripVietnameseDiacritics(text)
    .replace(/(\d)[.,](?=\d{3}(?!\d))/g, '$1')
    .replace(/(\d+)\s*d(?=\s|$|[.,!?;:)])/g, '$1 dong ')
    .replace(/(\d+)\s*k(?=\s|$|[.,!?;:)])/g, '$1 nghin ')
    .replace(/(\d+)\s*%/g, '$1 phan tram ')
    .replace(/(\d+)\s*(kg|gr|gam|g|ml|lit|l)(?=\s|$|[.,!?;:)])/g, '$1 $2 ')
    .replace(/\b(?:ki\s*lo\s*gam|kilogam|ky)\b/g, 'kg')
    .replace(/\b(?:mi\s*li\s*lit|mililit)\b/g, 'ml')
    .replace(/\b(?:gam|gr)\b/g, 'g')
    .replace(/\bngan\b/g, 'nghin')
  return prepared.match(/[a-z0-9]+/g) ?? []
}

function hasOrderedWindowMatch(needle: string[], haystack: string[], maxWindowSpan: number): boolean {
  if (!needle.length || haystack.length < needle.length) return false
  for (let start = 0; start < haystack.length; start += 1) {
    if (haystack[start] !== needle[0]) continue
    let needleIndex = 1
    const limit = Math.min(haystack.length, start + maxWindowSpan)
    for (let cursor = start + 1; cursor < limit && needleIndex < needle.length; cursor += 1) {
      if (haystack[cursor] === needle[needleIndex]) needleIndex += 1
    }
    if (needleIndex === needle.length) return true
  }
  return false
}

function hasContiguousNgramMatch(needle: string[], haystackPhrase: string, size: number): boolean {
  if (needle.length < size) return false
  for (let start = 0; start <= needle.length - size; start += 1) {
    const chunk = needle.slice(start, start + size).join(' ')
    if (haystackPhrase.includes(chunk)) return true
  }
  return false
}

/**
 * Detect when full-duplex barge-in or post-speech reverberation accidentally
 * captures Mai's own loudspeaker TTS instead of a real customer utterance.
 * Whitelists common short customer commands while rejecting verbatim,
 * diacritic/unit-shifted, and partially misheard assistant bleedthrough.
 */
export function isLikelyAssistantEcho(
  transcript: string,
  spokenText: string | null | undefined,
  options?: AssistantEchoOptions,
): boolean {
  if (!spokenText) return false
  const strict = Boolean(options?.strict)
  const transcriptTokens = tokenizeVietnameseText(transcript)
  const spokenTokens = tokenizeVietnameseText(spokenText)
  if (!transcriptTokens.length || spokenTokens.length < 2) return false

  const transcriptPhrase = transcriptTokens.join(' ')
  if (LEGITIMATE_SHORT_CUSTOMER_PHRASES.has(transcriptPhrase)) return false

  const normTranscriptTokens = normalizeRetailEchoTokens(transcript)
  const normSpokenTokens = normalizeRetailEchoTokens(spokenText)
  if (!normTranscriptTokens.length || normSpokenTokens.length < 2) return false

  const normTranscriptPhrase = normTranscriptTokens.join(' ')
  const normSpokenPhrase = normSpokenTokens.join(' ')
  const spokenPhrase = spokenTokens.join(' ')
  const startsWithCustomerCommand = CUSTOMER_INTERRUPTION_PREFIXES.some(
    (prefix) => normTranscriptPhrase === prefix || normTranscriptPhrase.startsWith(`${prefix} `),
  )

  if (normTranscriptTokens.length === 1) {
    if (!strict) return false
    const token = normTranscriptTokens[0]
    return STRICT_SINGLE_TOKEN_ECHO_FRAGMENTS.has(token) && normSpokenTokens.includes(token)
  }

  if (normTranscriptTokens.length === 2) {
    if (!strict || startsWithCustomerCommand) return false
    return spokenPhrase.includes(transcriptPhrase) || normSpokenPhrase.includes(normTranscriptPhrase)
  }

  if (spokenPhrase.includes(transcriptPhrase) || normSpokenPhrase.includes(normTranscriptPhrase)) {
    return true
  }

  if (normTranscriptTokens.length === 3) {
    if (!strict || startsWithCustomerCommand) return false
    return hasOrderedWindowMatch(normTranscriptTokens, normSpokenTokens, 5)
  }

  if (!startsWithCustomerCommand && hasContiguousNgramMatch(normTranscriptTokens, normSpokenPhrase, 4)) {
    return true
  }

  const spokenSet = new Set(normSpokenTokens)
  let overlap = 0
  for (const token of normTranscriptTokens) {
    if (spokenSet.has(token)) overlap += 1
  }
  const overlapRatio = overlap / normTranscriptTokens.length
  if (overlapRatio < 0.55) return false

  const spokenBigrams = new Set<string>()
  for (let i = 0; i < normSpokenTokens.length - 1; i += 1) {
    spokenBigrams.add(`${normSpokenTokens[i]}\u0000${normSpokenTokens[i + 1]}`)
  }
  let sharedBigrams = 0
  for (let i = 0; i < normTranscriptTokens.length - 1; i += 1) {
    if (spokenBigrams.has(`${normTranscriptTokens[i]}\u0000${normTranscriptTokens[i + 1]}`)) {
      sharedBigrams += 1
    }
  }
  return sharedBigrams >= 2 || (overlapRatio >= 0.65 && sharedBigrams >= 1)
}

export type TurnCompleteness = 'complete' | 'incomplete' | 'ambiguous'

const VI_INCOMPLETE_TAILS = new Set([
  'là', 'mà', 'và', 'với', 'hoặc', 'hay', 'nhưng', 'nhưng mà',
  'vì', 'bởi vì', 'tại vì', 'nên', 'cho nên', 'thì', 'của', 'cho',
  'từ', 'đến', 'tới', 'ở', 'trong', 'trên', 'dưới', 'về', 'theo', 'bằng', 'như',
  'cái', 'chiếc', 'loại', 'chai', 'lon', 'hộp', 'gói', 'lốc', 'thùng', 'bịch', 'túi', 'hũ', 'lọ', 'tuýp',
  'có', 'muốn', 'cần', 'đang', 'định', 'hỏi', 'xem', 'tìm', 'mua', 'lấy', 'thêm', 'chọn', 'dùng', 'để',
  'nếu', 'khi', 'lúc', 'với cả', 'với lại', 'còn', 'cũng', 'kiểu', 'giống',
  'ờ', 'à', 'ừm', 'âm', 'ơ', 'ê',
])

const VI_INCOMPLETE_PHRASE_TAILS = [
  'cho mình hỏi',
  'cho tôi hỏi',
  'cho em hỏi',
  'mình muốn hỏi',
  'mình muốn mua',
  'mình đang tìm',
  'mình cần tìm',
  'mình cần mua',
  'cho mình xem',
  'cho tôi xem',
  'ở đây có',
  'cửa hàng có',
  'bên mình có',
  'bên bạn có',
  'tư vấn giúp mình',
  'tư vấn cho mình',
  'cho mình lấy',
  'lấy cho mình',
  'tìm giúp mình',
  'ví dụ như',
  'nghĩa là',
  'tức là',
  'với cả',
  'với lại',
  'nhưng mà',
  'thế thì',
]

const VI_COMPLETE_SHORT_PHRASES = new Set([
  'có', 'không', 'ok', 'được', 'vâng', 'dạ', 'rồi', 'xong rồi',
  'đúng rồi', 'không phải', 'cảm ơn', 'cảm ơn nhé', 'cảm ơn bạn',
  'tạm biệt', 'xin chào', 'chào mai', 'dừng lại', 'khoan đã',
  'thôi không cần', 'không cần đâu', 'thêm vào giỏ', 'bỏ ra giỏ',
  'thanh toán', 'tính tiền', 'xem giỏ hàng', 'gọi quản lý', 'gặp quản lý',
  'giá bao nhiêu', 'bao nhiêu tiền', 'ở kệ nào', 'ở đâu',
  'rẻ hơn không', 'có loại nào', 'có loại khác không',
  'lấy cái này', 'chọn cái này',
])

const VI_COMPLETE_ENDING_TOKENS = new Set([
  'không', 'chưa', 'nào', 'đâu', 'nhiêu', 'mấy', 'gì', 'sao',
  'nhé', 'nha', 'ạ', 'nhỉ', 'chứ', 'hả', 'vậy', 'thế', 'đấy', 'đó',
  'này', 'thôi', 'đi', 'luôn', 'ngay', 'nhất', 'hơn', 'rồi',
])

/**
 * Fast 0 ms Vietnamese lexical end-of-turn classifier mirroring
 * `backend/services/smart_turn.py` so complete sentences finalize immediately
 * while dangling clauses hold open instead of cutting the customer off mid-thought.
 */
export function classifyVietnameseTurnCompleteness(
  partialText: string | null | undefined,
): TurnCompleteness {
  const trimmed = (partialText ?? '').trim()
  if (!trimmed) return 'ambiguous'
  if (trimmed.endsWith('?') || trimmed.endsWith('!')) return 'complete'
  const tokens = tokenizeVietnameseText(trimmed)
  if (!tokens.length) return 'ambiguous'
  const phrase = tokens.join(' ')
  if (VI_COMPLETE_SHORT_PHRASES.has(phrase)) return 'complete'
  for (const suffix of VI_INCOMPLETE_PHRASE_TAILS) {
    if (phrase === suffix || phrase.endsWith(` ${suffix}`)) return 'incomplete'
  }
  const lastToken = tokens[tokens.length - 1]
  const lastTwo = tokens.length >= 2 ? `${tokens[tokens.length - 2]} ${lastToken}` : ''
  if (VI_INCOMPLETE_TAILS.has(lastTwo) || VI_INCOMPLETE_TAILS.has(lastToken)) {
    return 'incomplete'
  }
  if (tokens.length >= 3 && VI_COMPLETE_ENDING_TOKENS.has(lastToken)) {
    return 'complete'
  }
  return 'ambiguous'
}
