// Development-only browser probe. It uses real Silero/WASM + audio playback,
// synthetic microphone PCM, and mocked STT/chat so provider quotas cannot hide
// a playback/state regression. Import manually through the local dev browser.
import { VoiceConversationController } from '/src/voice-controller.ts'

export async function install(fixtureUrl) {
  const nativeFetch = window.fetch.bind(window)
  const nativeGetMedia = navigator.mediaDevices.getUserMedia
  const nativeStart = VoiceConversationController.prototype.start
  const fixture = await (await nativeFetch(fixtureUrl)).arrayBuffer()
  const contexts = [], sources = [], events = [], uploads = [], seen = new WeakSet()
  let captures = 0, responses = 0
  const time = () => Math.round(performance.now())
  const audio = document.querySelector('audio')
  const mark = name => events.push({ event: name, at_ms: time() })
  const onPlaying = () => mark('tts_playing')
  const onPause = () => mark('tts_paused')
  audio.addEventListener('playing', onPlaying)
  audio.addEventListener('pause', onPause)
  VoiceConversationController.prototype.start = function () {
    if (!seen.has(this)) {
      seen.add(this)
      const callback = this.options.onSpeechStart
      this.options.onSpeechStart = () => {
        mark('vad_confirmed_onset')
        callback?.()
        events.push({ event: 'after_onset_handler', at_ms: time(),
          audio_paused: audio.paused, audio_has_src: audio.hasAttribute('src'),
          capture_active: this.isActive })
      }
    }
    return nativeStart.call(this)
  }
  navigator.mediaDevices.getUserMedia = async () => {
    const capture = ++captures
    const context = new AudioContext({ sampleRate: 16000 })
    contexts.push(context)
    const destination = context.createMediaStreamDestination()
    const source = context.createBufferSource()
    source.buffer = await context.decodeAudioData(fixture.slice(0))
    source.connect(destination)
    sources.push(source)
    await context.resume()
    const begin = () => {
      mark(`input_${capture}_scheduled`)
      source.start(context.currentTime + .6)
    }
    if (capture === 1) begin()
    else if (capture === 2) {
      if (!audio.paused && audio.currentTime > 0) begin()
      else audio.addEventListener('playing', begin, { once: true })
    }
    mark(`capture_${capture}_opened`)
    return destination.stream
  }
  window.fetch = async (input, init) => {
    const url = String(input)
    if (url.includes('/audio/transcribe')) {
      const blob = init.body.get('file')
      uploads.push({ at_ms: time(), bytes: blob.size, type: blob.type })
      return Response.json({ text: uploads.length === 1
        ? 'Cho mình thịt bò để nhúng lẩu.' : 'Đổi sang thịt heo giúp mình.',
        speech_detected: true })
    }
    if (url.includes('/agent/respond')) {
      responses++
      if (responses > 1) return Response.json({ detail: 'Probe complete' }, { status: 503 })
      return Response.json({ state: 'BROWSING', text:
        'Bạn có thể chọn thịt bò thái mỏng hoặc thịt ba chỉ để nhúng lẩu. '
        + 'Nếu thích mềm và có chút mỡ thì ba chỉ sẽ hợp hơn. '
        + 'Bạn định ăn mấy người để mình gợi ý lượng thịt vừa đủ nhé?' })
    }
    return nativeFetch(input, init)
  }
  const probe = {
    snapshot: () => ({ physical_microphone: false, real_vad: true,
      mocked_stt_and_chat: true, cloud_tts: true, captures, uploads, events,
      audio_paused: audio.paused, audio_has_src: audio.hasAttribute('src'),
      contexts: contexts.map(c => c.state) }),
    cleanup: async () => {
      window.fetch = nativeFetch
      navigator.mediaDevices.getUserMedia = nativeGetMedia
      VoiceConversationController.prototype.start = nativeStart
      audio.removeEventListener('playing', onPlaying)
      audio.removeEventListener('pause', onPause)
      for (const source of sources) { try { source.stop() } catch {} }
      await Promise.all(contexts.map(c => c.close()))
    },
  }
  window.__bargeProbe = probe
  return probe.snapshot()
}
