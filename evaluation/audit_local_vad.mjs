/** Replay licensed cached audio through the installed model and frame processor.
 * This does not emulate browser capture, acoustic echo cancellation or a room mic.
 */
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const frontend = path.join(root, 'apps/kiosk-airi')
const require = createRequire(path.join(frontend, 'package.json'))
const ort = require('onnxruntime-web/wasm')
const vadRoot = path.dirname(require.resolve('@ricky0123/vad-web'))
const { Silero } = require(path.join(vadRoot, 'models/silero.js'))
const { FrameProcessor } = require(path.join(vadRoot, 'frame-processor.js'))
ort.env.wasm.numThreads = 1
const modelPath = path.join(frontend, 'public/vad/silero_vad_v6.onnx')
const modelBytes = fs.readFileSync(modelPath)
const model = await Silero.new(ort, async () => modelBytes.buffer.slice(
  modelBytes.byteOffset, modelBytes.byteOffset + modelBytes.byteLength,
))
const config = { positiveSpeechThreshold: 0.55, negativeSpeechThreshold: 0.35,
  redemptionMs: 900, preSpeechPadMs: 600, minSpeechMs: 250, submitUserSpeechOnPause: false }
// Fail if production settings change: results must correspond to deployed config.
const source = fs.readFileSync(path.join(frontend, 'src/voice-neural.ts'), 'utf8')
for (const [key, value] of Object.entries(config)) {
  if (!source.includes(`${key}: ${value}`)) throw Error(`Config mismatch: ${key}`)
}
const manifest = JSON.parse(fs.readFileSync(path.join(root, '.tmp/fleurs_vi_test_samples/manifest.json')))
const files = manifest.samples.map(s => ({ id: `fleurs:${s.filename}`,
  file: path.join(root, '.tmp/fleurs_vi_test_samples', s.filename), dataset: 'google/fleurs vi_vn test; CC-BY-4.0' }))
const regional = JSON.parse(fs.readFileSync(path.join(root, 'evaluation/results/live_groq_stt_vivoice34_current_20261026.json')))
files.push(...regional.results.map(s => ({ id: `vivoice:${s.province}`, file: path.join(root, '.tmp/vivoice34_samples', s.file),
  dataset: 'anonymous-vivoice34/ViVoice34 train preview; CC-BY-4.0; province is not a verified accent' })))
const diagnosticManifestPath = process.argv[3] ? path.resolve(process.argv[3]) : null
if (diagnosticManifestPath) {
  const diagnosticManifest = JSON.parse(fs.readFileSync(diagnosticManifestPath))
  files.push(...diagnosticManifest.samples.map(s => ({ id: s.id,
    file: path.join(root, s.path), dataset: `${s.source}; ${s.license}; ${s.split}`,
    expected: s.kind === 'human-speech' ? 'speech' : 'non-speech-control' })))
}
const output = process.argv[2] ? path.resolve(process.argv[2]) : path.join(root,
  `docs/reports/artifacts/local_vad_audio_replay_${new Date().toISOString().replace(/[:.]/g, '-')}.json`)
fs.mkdirSync(path.dirname(output), { recursive: true })
const report = { created_at: new Date().toISOString(), kind: 'installed-v6-onnx-wasm-replay',
  limitations: ['Exposed diagnostic samples, no turn-boundary reference labels.',
    'No browser AudioWorklet, room microphone, echo cancellation or playback test.'],
  model_sha256: createHash('sha256').update(modelBytes).digest('hex'),
  config_source_sha256: createHash('sha256').update(source).digest('hex'),
  diagnostic_manifest_sha256: diagnosticManifestPath
    ? createHash('sha256').update(fs.readFileSync(diagnosticManifestPath)).digest('hex') : null,
  config, rows: [] }
let firstSpeech
for (const item of [...files, { id: 'digital-silence', samples: new Float32Array(48_000) },
  { id: 'steady-440hz', samples: Float32Array.from({ length: 48_000 }, (_, i) => 0.15 * Math.sin(i * 2 * Math.PI * 440 / 16_000)) },
  { id: 'seeded-white-noise', samples: (() => { let seed = 42; return Float32Array.from({ length: 48_000 }, () => {
    seed = (1664525 * seed + 1013904223) >>> 0; return (seed / 2 ** 32 - 0.5) * 0.1
  }) })() }, { id: 'short-human-fragment', short: true }]) {
  let samples = item.samples
  let audioHash
  if (item.file) {
    const result = spawnSync('ffmpeg', ['-v', 'error', '-i', item.file, '-ar', '16000', '-ac', '1', '-f', 'f32le', 'pipe:1'], { maxBuffer: 64 * 1024 ** 2 })
    if (result.status !== 0) throw Error(`Decode failed: ${item.id}`)
    const bytes = result.stdout
    samples = new Float32Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength))
    audioHash = createHash('sha256').update(fs.readFileSync(item.file)).digest('hex')
    firstSpeech ??= samples
  }
  if (item.short) samples = firstSpeech.slice(16_000, 16_000 + 1600)
  const audio = new Float32Array(samples.length + 19_200)
  audio.set(samples)
  const events = []
  let frameTime = 0
  const processor = new FrameProcessor(model.process, model.reset_state, config, 32)
  processor.resume()
  const start = performance.now()
  for (let offset = 0; offset + 512 <= audio.length; offset += 512) {
    frameTime = (offset + 512) / 16000
    await processor.process(audio.slice(offset, offset + 512), event => {
      if (event.msg !== 'FRAME_PROCESSED') events.push({ event: event.msg, at_seconds: frameTime,
        ...(event.audio ? { duration_seconds: event.audio.length / 16000 } : {}) })
    })
  }
  processor.pause(() => {})
  report.rows.push({ id: item.id, dataset: item.dataset, expected: item.expected, audio_sha256: audioHash,
    input_seconds: samples.length / 16000, elapsed_ms: Math.round(performance.now() - start),
    events: events.filter(e => !e.event.toLowerCase().includes('frame')) })
  fs.writeFileSync(output, JSON.stringify(report, null, 2))
  console.log(item.id, JSON.stringify(report.rows.at(-1).events))
}
await model.release()
