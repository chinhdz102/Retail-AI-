import type { LanguageCode, TranscriptionResponse } from './types'
import type { VoiceCaptureMetadata } from './voice-controller'

/** One opt-in turn in RAM. Epochs prevent disabled/cleared requests restoring it. */
export class LocalVoiceDiagnostics {
  private epoch = 0
  audioUrl = ''
  jsonUrl = ''

  clear() {
    this.epoch += 1
    if (this.audioUrl) URL.revokeObjectURL(this.audioUrl)
    if (this.jsonUrl) URL.revokeObjectURL(this.jsonUrl)
    this.audioUrl = ''
    this.jsonUrl = ''
  }

  begin(audio: Blob, language: LanguageCode, capture?: VoiceCaptureMetadata) {
    this.clear()
    const epoch = this.epoch
    this.audioUrl = URL.createObjectURL(audio)
    const metadata = { capturedAt: new Date().toISOString(), language,
      audio: { type: audio.type, bytes: audio.size }, capture }
    const update = (response?: TranscriptionResponse) => {
      if (epoch !== this.epoch) return false
      if (this.jsonUrl) URL.revokeObjectURL(this.jsonUrl)
      this.jsonUrl = URL.createObjectURL(new Blob([
        JSON.stringify({ ...metadata, response }, null, 2),
      ], { type: 'application/json' }))
      return true
    }
    update()
    return update
  }
}
