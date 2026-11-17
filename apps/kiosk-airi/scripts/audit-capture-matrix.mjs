import { chromium } from 'playwright'
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { resolve } from 'node:path'
import { parseArgs } from 'node:util'

const { values } = parseArgs({ options: {
  cases: { type: 'string' }, output: { type: 'string' },
  url: { type: 'string', default: 'http://127.0.0.1:5173' },
  backend: { type: 'string' },
}})
if (!values.cases || !values.output) throw new Error('Pass --cases and --output')
const cases = JSON.parse(readFileSync(values.cases, 'utf8'))
const browser = await chromium.launch({ channel: 'chrome', headless: true,
  args: ['--autoplay-policy=no-user-gesture-required'] })
const rows = [], errors = []
mkdirSync(resolve('.tmp/browser-qa/captures'), { recursive: true })
try {
  const page = await browser.newPage()
  page.on('pageerror', error => errors.push(String(error)))
  await page.goto(values.url + '/evaluation/capture-probe.html')
  await page.waitForFunction(() => window.probe?.ready)
  let sessionId
  if (values.backend) {
    const response = await fetch(values.backend + '/sessions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kiosk_id: 'browser-capture-audit' }),
    })
    if (!response.ok) throw new Error('Cannot create audit session')
    sessionId = (await response.json()).session_id
  }
  try {
    for (const [index, item] of cases.entries()) {
      const result = await page.evaluate(samples => probe.run(samples), item.samples)
      const row = { id: item.id, kind: item.kind, gain: item.gain, ...result }
      for (const [captureIndex, capture] of row.captures.entries()) {
        const bytes = Buffer.from(capture.bytes)
        delete capture.bytes
        capture.sha256 = createHash('sha256').update(bytes).digest('hex')
        capture.path = resolve(`.tmp/browser-qa/captures/${capture.sha256}.wav`)
        writeFileSync(capture.path, bytes)
        if (sessionId) {
          const form = new FormData()
          form.append('session_id', sessionId); form.append('language', 'vi')
          form.append('file', new Blob([bytes], { type: 'audio/wav' }), 'voice.wav')
          const response = await fetch(values.backend + '/audio/transcribe', { method: 'POST', body: form })
          capture.httpStatus = response.status
          capture.result = await response.json()
        }
      }
      rows.push(row)
      writeFileSync(values.output, JSON.stringify({ physicalMicrophone: false, rows, errors }, null, 2))
      console.log(JSON.stringify({ id: item.id, gain: item.gain,
        readyMs: Math.round(result.readyMs), captures: row.captures.length,
        text: row.captures[0]?.result?.text, errors: result.errors }))
    }
  } finally {
    if (sessionId) await fetch(values.backend + `/sessions/${sessionId}/close`, { method: 'POST' })
  }
} finally { await browser.close() }
process.exitCode = errors.length || rows.some(row => row.errors.length) ? 1 : 0
