// Development-only: real browser VAD, real Azure STT/TTS, no LLM or physical mic.
import { VoiceConversationController } from '../src/voice-controller'
import { TranscriptionPreview } from '../src/voice-preview'
import { createSession, closeSession, prepareVoice, transcribe, createSynthesisTicket } from '../src/api'
import type { TranscriptionResponse } from '../src/types'

const browser = window as any
browser.probe = { events: [], done: false, error: '', text: '', uploads: [] }
const mark = (event: string, extra = {}) => {
  browser.probe.events.push({ event, at_ms: Math.round(performance.now()), ...extra })
}
document.querySelector('#start')!.addEventListener('click', async () => {
  let controller: VoiceConversationController | undefined
  let context: AudioContext | undefined
  let session: string | undefined
  const preview = new TranscriptionPreview<TranscriptionResponse>()
  const usePreview = new URLSearchParams(location.search).get('preview') !== 'off'
  const upload = async (audio: Blob, signal?: AbortSignal) => {
    const row = { start_ms: Math.round(performance.now()), bytes: audio.size, complete_ms: 0, cancelled: false }
    browser.probe.uploads.push(row)
    try { return await transcribe(session!, audio, 'vi', signal) }
    finally { row.complete_ms = Math.round(performance.now()); row.cancelled = signal?.aborted ?? false }
  }
  try {
    session = (await createSession('browser-latency-audit')).session_id
    await prepareVoice(session)
    const fixture = await (await fetch('/evaluation/speech.wav')).arrayBuffer()
    context = new AudioContext({ sampleRate: 16000 })
    const buffer = await context.decodeAudioData(fixture)
    const destination = context.createMediaStreamDestination()
    const source = context.createBufferSource()
    source.buffer = buffer
    source.connect(destination)
    let finished!: () => void
    const endpoint = new Promise<void>(resolve => { finished = resolve })
    controller = new VoiceConversationController({
      getUserMedia: async () => destination.stream,
      onState: state => mark(`state_${state.phase}`),
      onSpeechStart: () => mark('confirmed_onset'),
      onVadFrame: probability => {
        if (probability >= .5) browser.probe.last_speech_ms = Math.round(performance.now())
      },
      ...(usePreview ? {
        onAudioPreview: (audio: Blob, id: number) => {
          mark('preview_started', { id })
          preview.start(id, session!, signal => upload(audio, signal))
        },
        onAudioPreviewInvalidated: (id: number) => {
          mark('preview_invalidated', { id }); preview.cancel(id)
        },
      } : {}),
      onAudio: (audio, id) => {
        mark('final_endpoint', { preview_id: id })
        const taken = preview.take(id, session!)
        void (async () => {
          try {
            let result = taken ? await taken.result : null
            if (!result?.speech_detected || !result.text.trim()) result = await upload(audio)
            browser.probe.text = result.text
            browser.probe.quality = result.quality
            browser.probe.accepted = result.speech_detected
            mark('transcription_ready')
            const audioUrl = await createSynthesisTicket(session!, 'Mình đang nghe, bạn cứ nói tự nhiên nhé.')
            const playback = new Audio(audioUrl)
            playback.addEventListener('playing', () => mark('tts_playing'), { once: true })
            await playback.play()
            playback.pause()
            playback.removeAttribute('src')
            playback.load()
            finished()
          } catch (error) { browser.probe.error = String(error); finished() }
        })()
      },
      onError: error => { browser.probe.error = `${error.message}: ${String(error.cause)}`; finished() },
    })
    await context.resume()
    await controller.start()
    source.start(context.currentTime + .4)
    mark('source_scheduled')
    await endpoint
  } catch (error) { browser.probe.error = String(error) }
  finally {
    controller?.stop()
    preview.cancel()
    await context?.close()
    if (session) await closeSession(session)
    browser.probe.done = true
  }
})
