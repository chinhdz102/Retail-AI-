import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { chromium } from 'playwright-core'

const origin = 'http://127.0.0.1:5173'
const candidates = [process.env.CHROME_BIN, '/usr/bin/google-chrome',
  '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser']
const executablePath = candidates.find((item) => item && existsSync(item))
if (!executablePath) throw new Error('No installed Chromium/Chrome binary for VAD smoke test')

const server = spawn('npm', ['run', 'dev', '--', '--host', '127.0.0.1'], {
  cwd: new URL('../', import.meta.url).pathname,
  stdio: ['ignore', 'pipe', 'pipe'],
})
let browser
try {
  let ready = false
  for (let attempt = 0; attempt < 80; attempt++) {
    try {
      const response = await fetch(origin, { signal: AbortSignal.timeout(1000) })
      if (response.ok) { ready = true; break }
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 250))
  }
  if (!ready) throw new Error('Vite server did not start')

  browser = await chromium.launch({
    executablePath, headless: true,
    args: ['--no-sandbox', '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'],
  })
  const context = await browser.newContext({ permissions: ['microphone'] })
  const page = await context.newPage()
  const vadRequests = []
  page.on('request', request => {
    if (request.url().includes('/vad/')) vadRequests.push(request.url())
  })
  await page.goto(origin, { waitUntil: 'domcontentloaded' })
  await page.locator('body').click()
  const result = await page.evaluate(async () => {
    const { createLocalNeuralVad } = await import('/src/voice-neural.ts')
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
    let vad
    try {
      vad = await createLocalNeuralVad({
        getStream: async () => stream,
        onSpeechStart: () => {},
        onSpeechEnd: () => {},
        onVADMisfire: () => {},
        onFrameProcessed: () => {},
      })
      await vad.start()
      await new Promise(resolve => setTimeout(resolve, 600))
      return { initialized: true, sampleRate: stream.getAudioTracks()[0]?.getSettings().sampleRate }
    } finally {
      await vad?.destroy()
      stream.getTracks().forEach(track => track.stop())
    }
  })
  if (!result.initialized) throw new Error('Neural VAD did not start in Chromium')
  if (!vadRequests.some(url => url.includes('silero_vad_v5.onnx'))) {
    throw new Error('Local Silero v5 model was not requested')
  }
  if (vadRequests.some(url => !url.startsWith(origin))) {
    throw new Error('Unexpected VAD asset request to third-party origin')
  }
  console.log(JSON.stringify({
    status: 'passed', browser: executablePath,
    modelRequests: vadRequests.filter(url => url.endsWith('.onnx')).length,
    wasmRequests: vadRequests.filter(url => url.endsWith('.wasm')).length,
    note: 'Fake browser microphone only; not physical-acoustic accuracy',
  }))
} finally {
  if (browser) await browser.close()
  server.kill('SIGTERM')
}
