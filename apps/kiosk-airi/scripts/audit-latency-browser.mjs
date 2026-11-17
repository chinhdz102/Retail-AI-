import { chromium } from 'playwright'
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../../../', import.meta.url))
const fixtureDir = resolve(root, 'docs/reports/artifacts/stt-input-captures')
const browser = await chromium.launch({ channel: 'chrome', headless: true,
  args: ['--autoplay-policy=no-user-gesture-required'] })
const rows = []
try {
  for (const test of [
    ...['off', 'on', 'off', 'on'].map(mode => ({ mode, fixture: 'vi-VN-NamMinhNeural-01-raw.wav', expected: 'thịt bò' })),
    { mode: 'on', fixture: 'NamMinh-pause-raw.wav', expected: 'Không đường' },
    { mode: 'on', fixture: 'vi-VN-NamMinhNeural-06-fan5db-raw.wav', expected: '' },
  ]) {
    const { mode } = test
    const fixture = readFileSync(resolve(fixtureDir, test.fixture))
    const page = await browser.newPage()
    await page.route('**/evaluation/speech.wav', route => route.fulfill({ body: fixture, contentType: 'audio/wav' }))
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto(`http://127.0.0.1:5173/evaluation/latency-probe.html?preview=${mode}`)
    await page.click('#start')
    await page.waitForFunction(() => window.probe.done, null, { timeout: 60000 })
    const row = await page.evaluate(() => window.probe)
    const when = name => row.events.find(event => event.event === name)?.at_ms
    row.preview = mode === 'on'
    row.fixture = test.fixture
    row.expected = test.expected
    row.errors = errors
    row.endpoint_to_stt_ms = when('transcription_ready') - when('final_endpoint')
    row.last_speech_to_stt_ms = when('transcription_ready') - row.last_speech_ms
    row.last_speech_to_tts_playing_ms = when('tts_playing') - row.last_speech_ms
    rows.push(row)
    await page.close()
  }
} finally { await browser.close() }
const report = { physical_microphone: false, llm_called: false, real_browser_vad: true,
  real_cloud_stt_tts: true, synthetic_voice: true, rows }
writeFileSync(resolve(root, process.env.VOICE_LATENCY_REPORT || 'docs/reports/artifacts/voice_latency_browser_overlap_20261115.json'), JSON.stringify(report, null, 2))
console.log(JSON.stringify(report, null, 2))
process.exitCode = rows.every(row => !row.error && !row.errors.length && row.accepted
  && row.text.includes(row.expected)) ? 0 : 1
