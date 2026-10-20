/** Locally bundled, lazy-loaded neural VAD.
 * Model/WASM/worklet assets are copied by scripts/copy-vad-assets.mjs.
 * No customer audio is sent to a VAD provider or fetched from a CDN.
 */
export interface NeuralVadCallbacks {
  getStream: () => Promise<MediaStream>
  onSpeechStart: () => void
  onSpeechEnd: (audio: Float32Array) => void
  onVADMisfire: () => void
  onFrameProcessed: (probabilities: { isSpeech: number }, frame: Float32Array) => void
}

export interface NeuralVadSession {
  start: () => Promise<void> | void
  destroy: () => Promise<void> | void
}

export async function createLocalNeuralVad(callbacks: NeuralVadCallbacks): Promise<NeuralVadSession> {
  const { MicVAD } = await import('@ricky0123/vad-web')
  const base = import.meta.env.BASE_URL
  return MicVAD.new({
    model: 'v5',
    processorType: 'AudioWorklet',
    baseAssetPath: `${base}vad/`,
    onnxWASMBasePath: `${base}vad/onnx/`,
    positiveSpeechThreshold: 0.55,
    negativeSpeechThreshold: 0.35,
    redemptionMs: 650,
    preSpeechPadMs: 350,
    minSpeechMs: 250,
    submitUserSpeechOnPause: false,
    ...callbacks,
  })
}
