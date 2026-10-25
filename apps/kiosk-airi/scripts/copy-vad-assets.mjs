import { copyFile, mkdir, readdir, stat } from 'node:fs/promises'
import { resolve, join } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const sourceVad = join(root, 'node_modules/@ricky0123/vad-web/dist')
const sourceOrt = join(root, 'node_modules/onnxruntime-web/dist')
const destination = join(root, 'public/vad')
const targetOrt = join(destination, 'onnx')
await mkdir(destination, { recursive: true })
await mkdir(targetOrt, { recursive: true })
const files = (await readdir(sourceVad)).filter((name) =>
  name === 'vad.worklet.bundle.min.js' || /^silero_vad_.*\.onnx$/.test(name))
if (!files.includes('vad.worklet.bundle.min.js') || !files.includes('silero_vad_v5.onnx')) {
  throw new Error('The local Silero VAD package does not contain the required model/worklet assets')
}
for (const name of files) await copyFile(join(sourceVad, name), join(destination, name))
const runtime = (await readdir(sourceOrt)).filter((name) =>
  /\.(wasm|mjs)$/.test(name) && name.startsWith('ort-'))
if (!runtime.some((name) => name.endsWith('.wasm')) || !runtime.some((name) => name.endsWith('.mjs'))) {
  throw new Error('ONNX WASM runtime assets are missing')
}
for (const name of runtime) await copyFile(join(sourceOrt, name), join(targetOrt, name))
const sizes = await Promise.all([...files.map(name => join(destination, name)),
  ...runtime.map(name => join(targetOrt, name))].map(async name => (await stat(name)).size))
console.info('Local VAD assets prepared', files.length + runtime.length, 'files',
  Math.round(sizes.reduce((sum, size) => sum + size, 0) / 1024), 'KiB')
