// Full demo flow with real cloud calls; only microphone input is synthetic.
import { chromium } from 'playwright'
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../../../', import.meta.url))
const fixture = readFileSync(resolve(root, 'docs/reports/artifacts/stt-input-captures/vi-VN-NamMinhNeural-01-raw.wav'))
const browser = await chromium.launch({ channel: 'chrome', headless: true,
  args: ['--autoplay-policy=no-user-gesture-required'] })
const page = await browser.newPage()
const errors = [], network = []
page.on('pageerror', error => errors.push(error.message))
page.on('response', response => {
  if (response.url().includes(':8000/')) network.push({ url: response.url(), status: response.status() })
})
let result
try {
  await page.route('**/evaluation/speech.wav', route => route.fulfill({ body: fixture, contentType: 'audio/wav' }))
  await page.goto('http://127.0.0.1:5173/')
  await page.getByRole('button', { name: 'Bắt đầu nói', exact: true }).waitFor()
  await page.waitForFunction(() => !document.querySelector('button[aria-label="Bắt đầu nói"]').disabled)
  await page.evaluate(async () => {
    const { VoiceConversationController } = await import('/src/voice-controller.ts')
    const nativeStart = VoiceConversationController.prototype.start
    const contexts = [], events = []
    const fixture = await (await fetch('/evaluation/speech.wav')).arrayBuffer()
    let captures = 0
    const mark = event => events.push({ event, at_ms: Math.round(performance.now()) })
    document.querySelector('audio').addEventListener('playing', event => {
      if (event.target.src.includes('/audio/synthesize/stream/')) mark('tts_playing')
    })
    navigator.mediaDevices.getUserMedia = async () => {
      const context = new AudioContext({ sampleRate: 16000 })
      contexts.push(context)
      const destination = context.createMediaStreamDestination()
      if (++captures === 1) {
        const source = context.createBufferSource()
        source.buffer = await context.decodeAudioData(fixture.slice(0))
        source.connect(destination)
        source.start(context.currentTime + .6)
      }
      await context.resume()
      return destination.stream
    }
    VoiceConversationController.prototype.start = function () {
      if (!this.probeWrapped) {
        this.probeWrapped = true
        for (const key of ['onAudioPreview', 'onAudio', 'onSpeechStart']) {
          const callback = this.options[key]
          this.options[key] = (...args) => { mark(key); return callback?.(...args) }
        }
      }
      return nativeStart.call(this)
    }
    window.appLatencyProbe = { events, contexts }
  })
  await page.getByRole('button', { name: 'Bắt đầu nói', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.message-user')
    || document.querySelector('.chat-error'), null, { timeout: 20000 })
  await page.waitForFunction(() => document.querySelector('article.message-assistant')
    || document.querySelector('.chat-error'), null, { timeout: 65000 })
  await page.waitForFunction(() => window.appLatencyProbe.events.some(e => e.event === 'tts_playing')
    || document.querySelector('.chat-error'), null, { timeout: 15000 })
  result = await page.evaluate(() => ({
    events: window.appLatencyProbe.events,
    user: document.querySelector('.message-user')?.textContent,
    assistant: document.querySelector('article.message-assistant')?.textContent,
    error: document.querySelector('.chat-error')?.textContent,
  }))
  const stop = page.locator('.mic-button[aria-label="Dừng nghe"]')
  if (await stop.count()) await stop.click()
  await page.evaluate(() => Promise.all(window.appLatencyProbe.contexts.map(c => c.close())))
} catch (error) { result = { error: String(error) } }
finally { await browser.close() }
const report = { physical_microphone: false, synthetic_input: true,
  real_app_stt_llm_tts: true, result, errors, network }
writeFileSync(resolve(root, 'docs/reports/artifacts/voice_latency_app_20261115.json'), JSON.stringify(report, null, 2))
console.log(JSON.stringify(report, null, 2))
process.exitCode = !result.error && !errors.length && result.user?.includes('thịt bò')
  && result.assistant && result.events.some(e => e.event === 'tts_playing') ? 0 : 1
