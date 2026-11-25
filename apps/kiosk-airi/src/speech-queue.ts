export interface SpeakableChunkExtraction {
  chunks: string[]
  remainder: string
}

const MIN_CHUNK_CHARS = 24
const LONG_CLAUSE_TRIGGER_CHARS = 150
const MIN_CLAUSE_CHARS = 75

function isDecimalOrThousandsDot(text: string, index: number): boolean {
  if (text[index] !== '.') return false
  const prev = index > 0 ? text[index - 1] : ''
  const next = index + 1 < text.length ? text[index + 1] : ''
  return /\d/.test(prev) && /\d/.test(next)
}

function findNextSplitIndex(text: string): number {
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]
    // Keep commas and semicolons inside the same sentence so neural TTS reads
    // them with natural Vietnamese prosody instead of halting between clips.
    const isSentenceEnd =
      ch === '!' || ch === '?' || ch === '。' || ch === '！' || ch === '？'
      || ch === '\n'
      || (ch === '.' && !isDecimalOrThousandsDot(text, i))
    if (!isSentenceEnd) continue
    // Wait until a whitespace or CJK delimiter confirms the boundary isn't
    // mid-token (e.g. "29." before "000đ" arrives in the next SSE chunk).
    const next = i + 1 < text.length ? text[i + 1] : ''
    const confirmedBoundary =
      ch === '\n' || ch === '。' || ch === '！' || ch === '？'
      || (next !== '' && /[\s"'”’)\]]/.test(next))
    if (!confirmedBoundary) continue
    const candidate = text.slice(0, i + 1).trim()
    if (candidate.length >= MIN_CHUNK_CHARS) {
      return i + 1
    }
  }

  if (text.length >= LONG_CLAUSE_TRIGGER_CHARS) {
    for (let i = MIN_CLAUSE_CHARS; i < text.length - 1; i += 1) {
      const ch = text[i]
      if ((ch === ',' || ch === ';' || ch === '，' || ch === '—') && /\s/.test(text[i + 1] ?? '')) {
        return i + 1
      }
    }
  }

  return -1
}

/**
 * Incrementally extract natural speakable Vietnamese/EN/ZH sentences from a
 * streaming LLM buffer without splitting inside VND numbers like "29.000đ".
 */
export function extractSpeakableChunks(
  buffer: string,
  flush = false,
): SpeakableChunkExtraction {
  const chunks: string[] = []
  let working = buffer

  while (working.length > 0) {
    const splitAt = findNextSplitIndex(working)
    if (splitAt < 0) break
    const piece = working.slice(0, splitAt).trim()
    working = working.slice(splitAt).trimStart()
    if (piece) chunks.push(piece)
  }

  if (flush) {
    const tail = working.trim()
    if (tail) chunks.push(tail)
    return { chunks, remainder: '' }
  }

  return { chunks, remainder: working }
}
