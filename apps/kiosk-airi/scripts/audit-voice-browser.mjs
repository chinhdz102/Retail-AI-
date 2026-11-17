import { chromium } from 'playwright'
import { spawn } from 'node:child_process'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'
import { mkdirSync, writeFileSync } from 'node:fs'
const root = fileURLToPath(new URL('../../../', import.meta.url))
process.chdir(root)
const { values } = parseArgs({ options: {
  'speech-wav': { type: 'string' }, output: { type: 'string' },
}})
if (!values['speech-wav']) throw new Error('Pass --speech-wav=<16 kHz mono WAV with speech and trailing silence>')
mkdirSync(resolve('.tmp/browser-qa'), { recursive: true })
const silence = Buffer.alloc(44 + 16000 * 5 * 2)
silence.write('RIFF', 0); silence.writeUInt32LE(silence.length - 8, 4)
silence.write('WAVEfmt ', 8); silence.writeUInt32LE(16, 16)
silence.writeUInt16LE(1, 20); silence.writeUInt16LE(1, 22)
silence.writeUInt32LE(16000, 24); silence.writeUInt32LE(32000, 28)
silence.writeUInt16LE(2, 32); silence.writeUInt16LE(16, 34)
silence.write('data', 36); silence.writeUInt32LE(silence.length - 44, 40)
writeFileSync(resolve('.tmp/browser-qa/silence.wav'), silence)
const server = spawn(process.execPath, [
  resolve('apps/kiosk-airi/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', '5187', '--strictPort',
], { cwd: resolve('apps/kiosk-airi'), windowsHide: true, stdio: 'ignore' })
const rows = []
try {
  for (let attempt = 0; attempt < 50; attempt++) {
    try { if ((await fetch('http://127.0.0.1:5187/')).ok) break } catch {}
    await new Promise(done => setTimeout(done, 100))
  }
  for (const variant of ['silence', 'speech', 'cancel-start']) {
    const file = variant === 'silence' ? resolve('.tmp/browser-qa/silence.wav') : resolve(values['speech-wav'])
    const browser = await chromium.launch({
      channel: 'chrome', headless: true,
      args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
        '--use-file-for-fake-audio-capture=' + file, '--autoplay-policy=no-user-gesture-required'],
    })
    const context = await browser.newContext({ permissions: ['microphone'] })
    const page = await context.newPage()
    const errors = [], networkErrors = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
    page.on('requestfailed', request => networkErrors.push(request.url() + ': ' + request.failure()?.errorText))
    try {
      await page.goto('http://127.0.0.1:5187/evaluation/voice-probe.html')
      await page.waitForFunction(() => window.probe)
      await page.click('#start')
      if (variant === 'cancel-start') await page.click('#stop')
      else await page.waitForFunction(() => probe.levels > 5 || probe.errors.length, null, { timeout: 20000 })
      if (variant === 'speech') {
        await page.waitForFunction(() => probe.audio.length || probe.errors.length, null, { timeout: 20000 })
      } else {
        await page.waitForTimeout(5000)
      }
      const captured = await page.evaluate(() => probe.snapshot())
      await page.click('#stop')
      await page.waitForTimeout(200)
      const stopped = await page.evaluate(() => probe.snapshot())
      rows.push({ variant, captured, stopped, errors, networkErrors })
    } catch (error) { rows.push({ variant, error: String(error), errors, networkErrors }) }
    await browser.close()
  }
} finally {
  server.kill()
}
const valid = rows.length === 3 && rows.every(row => !row.error && !row.errors.length
  && !row.networkErrors.length && !row.captured.errors.length
  && !row.stopped.active && row.stopped.trackStates.every(s => s === 'ended')
  && row.stopped.contextStates.every(s => s === 'closed')
  && (row.variant === 'speech' ? row.captured.audio.length === 1
    && row.captured.audio[0].allTracksEnded : row.captured.audio.length === 0))
const report = { kind: 'real-Chrome-local-WASM-and-worklet-with-fake-device',
  physicalMicrophone: false, complete: valid, rows }
writeFileSync(resolve(values.output ?? 'docs/reports/artifacts/browser_whisper_capture_20261110.json'),
  JSON.stringify(report, null, 2))
console.log(JSON.stringify(report, null, 2))
process.exitCode = valid ? 0 : 1
