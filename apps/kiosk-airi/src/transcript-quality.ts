/** Detect long repeated-phrase loops sometimes hallucinated by speech recognition. */
export function isLikelyRepeatedSpeechTranscript(text: string): boolean {
  const tokens = text.toLocaleLowerCase('vi').match(/[\p{L}\p{N}]+/gu) ?? []
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
