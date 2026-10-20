/* AudioWorklet: capture mono PCM in bounded ~43ms transferable blocks.
 * Never routes microphone samples to the speaker: output is always silence.
 */
class RetailPcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.buffer = new Float32Array(2048)
    this.length = 0
  }

  process(inputs, outputs) {
    const channels = inputs[0] || []
    const first = channels[0]
    if (first) {
      for (let i = 0; i < first.length; i++) {
        let sample = 0
        for (let ch = 0; ch < channels.length; ch++) sample += channels[ch][i] || 0
        this.buffer[this.length++] = sample / channels.length
        if (this.length === this.buffer.length) {
          const block = this.buffer
          this.port.postMessage(block, [block.buffer])
          this.buffer = new Float32Array(2048)
          this.length = 0
        }
      }
    }
    for (const output of outputs) for (const channel of output) channel.fill(0)
    return true
  }
}
registerProcessor('retail-pcm-capture', RetailPcmCaptureProcessor)
