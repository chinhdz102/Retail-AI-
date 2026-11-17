// Exercise real getUserMedia processing, worklet, VAD and upload using fake mic.
// This is an automated test; it never opens a physical microphone.
import { chromium } from 'playwright'
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { resolve, dirname } from 'node:path'
import { parseArgs } from 'node:util'
const { values } = parseArgs({ options: {
  manifest: { type: 'string' }, output: { type: 'string' },
  url: { type: 'string', default: 'http://127.0.0.1:5173' },
  backend: { type: 'string', default: 'http://127.0.0.1:8000' },
} })
if (!values.manifest || !values.output) throw new Error('Pass --manifest and --output')
const manifest = JSON.parse(readFileSync(values.manifest, 'utf8'))
const report = { physicalMicrophone: false, kind: 'Chrome-fake-mic-APM-VAD-PCM-HTTP', rows: [] }
const output = resolve(values.output), folder = resolve(dirname(output), 'stt-input-captures')
mkdirSync(folder, { recursive: true })
const response = await fetch(values.backend + '/sessions', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ kiosk_id: 'stt-input-audit' }),
})
if (!response.ok) throw new Error('Cannot create evaluation session')
const sessionId = (await response.json()).session_id
try {
  for (const item of manifest.cases) {
    for (const processing of ['production', 'raw']) {
      const browser = await chromium.launch({ channel: 'chrome', headless: true, args: [
        '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
        '--use-file-for-fake-audio-capture=' + resolve(item.file),
        '--autoplay-policy=no-user-gesture-required',
      ] })
      const row = { id: item.id, reference: item.reference, sourceSha256: item.sha256,
        processing, browserVersion: browser.version(), errors: [] }
      try {
        const context = await browser.newContext({ permissions: ['microphone'] })
        const page = await context.newPage()
        page.on('pageerror', error => row.errors.push(error.message))
        await page.goto(values.url + '/evaluation/stt-capture-probe.html?processing=' + processing)
        await page.waitForFunction(() => window.probe?.ready)
        await page.click('#start')
        await page.waitForFunction(() => probe.audio.length || probe.errors.length, null, { timeout: 25000 })
        const snapshot = await page.evaluate(() => probe.snapshot())
        const name = item.id + '-' + processing
        const framePath = resolve(folder, name + '-frames.json')
        writeFileSync(framePath, JSON.stringify(snapshot.inputFrames))
        delete snapshot.inputFrames
        row.framePath = framePath
        row.capture = snapshot
        for (const audio of snapshot.audio) {
          const bytes = Buffer.from(audio.bytes)
          delete audio.bytes
          audio.sha256 = createHash('sha256').update(bytes).digest('hex')
          audio.path = resolve(folder, name + '.wav')
          writeFileSync(audio.path, bytes)
          const form = new FormData()
          form.append('session_id', sessionId); form.append('language', 'vi')
          form.append('file', new Blob([bytes], { type: 'audio/wav' }), 'voice.wav')
          const result = await fetch(values.backend + '/audio/transcribe', { method: 'POST', body: form })
          audio.httpStatus = result.status
          audio.result = await result.json()
        }
        await page.click('#stop')
      } catch (error) { row.errors.push(String(error)) }
      finally { await browser.close() }
      report.rows.push(row)
      writeFileSync(output, JSON.stringify(report, null, 2))
      console.log(JSON.stringify({ id: item.id, processing, errors: row.errors,
        settings: row.capture?.inputSettings, contextRates: row.capture?.contextRates,
        text: row.capture?.audio[0]?.result?.text }))
    }
  }
} finally { await fetch(values.backend + `/sessions/${sessionId}/close`, { method: 'POST' }) }
report.complete = report.rows.length === manifest.cases.length * 2 && report.rows.every(row =>
  !row.errors.length && !row.capture.errors.length && row.capture.audio.length === 1
  && row.capture.audio.every(audio => audio.allTracksEnded && audio.httpStatus === 200))
writeFileSync(output, JSON.stringify(report, null, 2))
process.exitCode = report.complete ? 0 : 1
