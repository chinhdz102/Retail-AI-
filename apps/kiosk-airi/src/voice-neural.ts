/** Locally bundled, lazy-loaded neural VAD.
 * Model/WASM/worklet assets are copied by scripts/copy-vad-assets.mjs.
 * No customer audio is sent to a VAD provider or fetched from a CDN.
 */
export interface NeuralVadCallbacks {
  getStream: () => Promise<MediaStream>
  onSpeechStart: () => void
  onSpeechRealStart: () => void
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
    // v6 retains the 512-sample interface. Stricter speech thresholds reject
    // continuous fan/HVAC airflow while 750ms redemption avoids cutting off
    // natural mid-sentence Vietnamese pauses.
    model: 'v6',
    processorType: 'AudioWorklet',
    baseAssetPath: `${base}vad/`,
    onnxWASMBasePath: `${base}vad/onnx/`,
    positiveSpeechThreshold: 0.72,
    negativeSpeechThreshold: 0.45,
    redemptionMs: 750,
    preSpeechPadMs: 280,
    minSpeechMs: 320,
    submitUserSpeechOnPause: false,
    ...callbacks,
  })
}
