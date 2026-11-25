/** Execute the installed AudioWorklet processors in 128-sample blocks in a VM.
 * Not a browser, microphone, AEC or real-time scheduling acceptance test.
 */
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { createHash } from 'node:crypto'
const root = path.resolve(import.meta.dirname, '..')
// Optional research dependency, deliberately outside the kiosk dependency tree.
// From the repository root:
// npm install --prefix .tmp/noise_research --no-save --package-lock=false @sapphi-red/web-noise-suppressor@0.4.1
const pkg = process.env.NOISE_SUPPRESSOR_DIST
  ? path.resolve(process.env.NOISE_SUPPRESSOR_DIST)
  : path.join(root, '.tmp/noise_research/node_modules/@sapphi-red/web-noise-suppressor/dist')
if (!fs.existsSync(pkg)) {
  throw Error('Research WASM assets missing. Install the pinned optional package as documented in this script, or set NOISE_SUPPRESSOR_DIST.')
}
const manifestPath = path.resolve(process.argv[2] ?? '.tmp/noise_ablation_20261103/manifest.json')
const manifest = JSON.parse(fs.readFileSync(manifestPath))
const report = { kind: 'installed-worklet-vm-replay', package_version: '0.4.1',
  limitations: ['No browser AEC/NS/AGC or physical microphone.', 'Exposed small diagnostic, not a blind benchmark.'],
  manifest_sha256: createHash('sha256').update(fs.readFileSync(manifestPath)).digest('hex'), rows: [] }
for (const name of ['rnnoise', 'gtcrn']) {
  let Processor
  const script = fs.readFileSync(path.join(pkg, name, 'workletProcessor.js'), 'utf8')
    .replaceAll('import.meta.url', JSON.stringify('https://localhost/noise/worklet.js'))
  const context = vm.createContext({ console, WebAssembly, TextDecoder, TextEncoder, URL,
    Float32Array, Uint8Array, Int8Array, Uint16Array, Int16Array, Int32Array, Uint32Array,
    Float64Array, BigInt64Array, BigUint64Array, ArrayBuffer, setTimeout, clearTimeout,
    sampleRate: 48000,
    AudioWorkletProcessor: class { port = { addEventListener() {} } },
    registerProcessor: (_id, ctor) => { Processor = ctor } })
  vm.runInContext(script, context)
  const wasm = fs.readFileSync(path.join(pkg, name + '.wasm'))
  for (const item of manifest.rows) {
    const bytes = fs.readFileSync(path.join(root, item.pcm_path))
    const audio = new Float32Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength))
    const processor = new Processor({ processorOptions: { maxChannels: 1,
      wasmBinary: wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength) } })
    const started = performance.now()
    // The installed wrapper exposes processor once asynchronous WASM init ends.
    while (!processor.processor) {
      if (performance.now() - started > 5000) throw Error('WASM initialization timed out')
      await new Promise(resolve => setTimeout(resolve, 1))
    }
    const initMs = performance.now() - started
    const block = new Float32Array(128)
    const output = new Float32Array(Math.ceil((audio.length + 4800) / 128) * 128)
    const processingAt = performance.now()
    for (let offset = 0; offset < output.length; offset += 128) {
      block.fill(0); block.set(audio.subarray(offset, Math.min(audio.length, offset + 128)))
      processor.process([[block]], [[output.subarray(offset, offset + 128)]], {})
    }
    const elapsed = performance.now() - processingAt
    processor.destroy()
    const outputPath = item.pcm_path.replace(/\.f32$/, `.${name}.f32`)
    fs.writeFileSync(path.join(root, outputPath), Buffer.from(output.buffer))
    report.rows.push({ id: item.id, method: name, input_path: item.pcm_path,
      output_path: outputPath, init_ms: initMs, processing_ms: elapsed,
      real_time_factor: elapsed / (audio.length / 48000 * 1000),
      worklet_sha256: createHash('sha256').update(script).digest('hex'),
      wasm_sha256: createHash('sha256').update(wasm).digest('hex') })
    console.log(name, item.id, Math.round(elapsed), 'ms')
  }
}
const output = path.resolve(process.argv[3] ?? 'docs/reports/artifacts/noise_worklet_replay_20261103.json')
fs.writeFileSync(output, JSON.stringify(report, null, 2))
