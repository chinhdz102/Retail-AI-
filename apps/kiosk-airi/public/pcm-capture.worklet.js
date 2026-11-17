// AudioContext runs at 16 kHz. Collect exactly 512 mono samples per Silero frame.
class RetailPcmCapture extends AudioWorkletProcessor {
  constructor() {
    super()
    this.frame = new Float32Array(512)
    this.offset = 0
  }
  process(inputs) {
    const channels = inputs[0]
    if (!channels?.length) return true
    for (let i = 0; i < channels[0].length; i++) {
      let sample = 0
      for (const channel of channels) sample += channel[i] ?? 0
      this.frame[this.offset++] = sample / channels.length
      if (this.offset === 512) {
        this.port.postMessage(this.frame, [this.frame.buffer])
        this.frame = new Float32Array(512)
        this.offset = 0
      }
    }
    return true
  }
}
registerProcessor('retail-pcm-capture', RetailPcmCapture)
