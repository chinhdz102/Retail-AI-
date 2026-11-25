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
    // v6 retains the 512-sample (32 ms) interface. Calibrated to match the backend
    // Silero v6 profile: 0.58 positive / 0.35 negative hysteresis lets softer
    // Vietnamese syllables reset the silence counter instead of chopping clauses
    // mid-sentence, while 950 ms redemption + Smart Turn v3 hold absorb natural
    // thinking pauses. Noise rejection is handled by the 90 Hz HPF + SNR/ZCR
    // continuity gate in VoiceConversationController.
    model: 'v6',
    processorType: 'AudioWorklet',
    baseAssetPath: `${base}vad/`,
    onnxWASMBasePath: `${base}vad/onnx/`,
    positiveSpeechThreshold: 0.58,
    negativeSpeechThreshold: 0.35,
    redemptionMs: 950,
    preSpeechPadMs: 360,
    minSpeechMs: 320,
    submitUserSpeechOnPause: false,
    ...callbacks,
  })
}
