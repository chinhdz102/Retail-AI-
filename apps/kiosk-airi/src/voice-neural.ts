import { SPEECH_ENDPOINT } from './voice-endpoint'
import { SpeechPreview } from './voice-preview'

/** Local Silero owns each input and AudioContext; immutable weights stay warm.
 * Avoid MicVAD's incomplete destroy path when permission/startup is cancelled.
 */
export interface NeuralVadCallbacks {
  getStream: () => Promise<MediaStream>
  onSpeechStart: () => void
  onSpeechRealStart: () => void
  onSpeechEnd: (audio: Float32Array, previewId?: number) => void
  onSpeechPreview?: (audio: Float32Array, previewId: number) => void
  onSpeechPreviewInvalidated?: (previewId: number) => void
  onVADMisfire: () => void
  onFrameProcessed: (probabilities: { isSpeech: number }, frame: Float32Array) => void
  onError: (error: unknown) => void
}
export interface NeuralVadSession {
  start: () => Promise<void> | void
  flush?: () => Promise<void> | void
  destroy: () => Promise<void> | void
}

// Weights are immutable; recurrent state remains private to each capture.
// Keep the loaded model across turns so reopening the mic does not reload WASM
// and reconstruct an inference session while the user is already speaking.
let modelPromise: Promise<{
  ort: typeof import('onnxruntime-web/wasm')
  session: import('onnxruntime-web').InferenceSession
  FrameProcessor: typeof import('@ricky0123/vad-web').FrameProcessor
  Message: typeof import('@ricky0123/vad-web').Message
}> | undefined

function loadModel() {
  modelPromise ??= (async () => {
    const [ort, { FrameProcessor, Message }] = await Promise.all([
      import('onnxruntime-web/wasm'), import('@ricky0123/vad-web'),
    ])
    const base = import.meta.env.BASE_URL
    ort.env.wasm.wasmPaths = new URL(`${base}vad/onnx/`, window.location.href).href
    ort.env.wasm.numThreads = 1
    const session = await ort.InferenceSession.create(`${base}vad/silero_vad_v6.onnx`, {
      executionProviders: ['wasm'],
    })
    return { ort, session, FrameProcessor, Message }
  })().catch(error => { modelPromise = undefined; throw error })
  return modelPromise
}

export async function createLocalNeuralVad(callbacks: NeuralVadCallbacks): Promise<NeuralVadSession> {
  const { ort, session, FrameProcessor, Message } = await loadModel()
  const base = import.meta.env.BASE_URL
  let context: AudioContext | null = null
  let stream: MediaStream | null = null
  let source: MediaStreamAudioSourceNode | null = null
  let node: AudioWorkletNode | null = null
  let mute: GainNode | null = null
  let closed = false
  let started = false
  let pending = Promise.resolve()
  let queued = 0
  let analysisPeak = 0
  let ready: (() => void) | undefined
  const preview = new SpeechPreview({
    ready: (audio, id) => callbacks.onSpeechPreview?.(audio, id),
    invalidated: id => callbacks.onSpeechPreviewInvalidated?.(id),
  })
  let lastProbability = 1
  const rate = new ort.Tensor('int64', BigInt64Array.of(16000n), [])
  const detector = () => {
    let state = new ort.Tensor('float32', new Float32Array(256), [2, 1, 128])
    let tail = new Float32Array(64)
    return {
      reset: () => {
        state = new ort.Tensor('float32', new Float32Array(256), [2, 1, 128])
        tail = new Float32Array(64)
      },
      process: async (frame: Float32Array) => {
        const input = new Float32Array(576)
        input.set(tail)
        input.set(frame, 64)
        const result = await session.run({
          input: new ort.Tensor('float32', input, [1, 576]), state, sr: rate,
        })
        state = result.stateN as typeof state
        tail = frame.slice(-64)
        const probability = Number(result.output?.data[0])
        if (!Number.isFinite(probability) || probability < 0 || probability > 1) {
          throw new Error('Bộ phát hiện tiếng nói trả kết quả không hợp lệ')
        }
        return probability
      },
    }
  }
  const rawDetector = detector(), quietDetector = detector()
  const processor = new FrameProcessor(async frame => {
    // Quiet microphones previously never reached server-side gain recovery.
    // Level only the detector input, keeping the original audio for Whisper.
    let peak = 0
    for (const sample of frame) peak = Math.max(peak, Math.abs(sample))
    analysisPeak = Math.max(peak, analysisPeak * .98)
    const gain = Math.max(1, Math.min(16, .8 / Math.max(analysisPeak, .0002)))
    const analysis = frame.map(sample => sample * gain)
    // Independent state prevents gain adaptation from degrading the normal
    // input path. Both paths analyse the same original frame, never replace it.
    const probability = Math.max(await rawDetector.process(frame), await quietDetector.process(analysis))
    return { isSpeech: probability, notSpeech: 1 - probability }
  }, () => { rawDetector.reset(); quietDetector.reset(); analysisPeak = 0 }, SPEECH_ENDPOINT, 32)

  const stopInput = () => {
    if (node) node.port.onmessage = null
    stream?.getTracks().forEach(track => track.stop())
    source?.disconnect()
    node?.disconnect()
    mute?.disconnect()
    void context?.close().catch(() => {})
  }
  const fail = (error: unknown) => {
    if (closed) return
    callbacks.onError(error)
  }
  const handle: Parameters<typeof processor.process>[1] = event => {
    if (closed) return
    switch (event.msg) {
      case Message.SpeechStart: callbacks.onSpeechStart(); break
      case Message.SpeechRealStart: callbacks.onSpeechRealStart(); break
      case Message.SpeechEnd: callbacks.onSpeechEnd(event.audio, preview.finish()); break
      case Message.VADMisfire: preview.reset(); callbacks.onVADMisfire(); break
      case Message.FrameProcessed:
        lastProbability = event.probs.isSpeech
        // FrameProcessed precedes SpeechEnd in vad-web. Invalidate on new
        // acoustic evidence before a boundary in this same frame can commit.
        preview.frame(lastProbability, [], false)
        callbacks.onFrameProcessed(event.probs, event.frame)
        break
    }
  }
  return {
    start: async () => {
      if (closed || started) return
      started = true
      try {
        // Let the browser resample the physical input once to the model's rate.
        context = new AudioContext({ sampleRate: 16000 })
        stream = await callbacks.getStream()
        if (closed) { stopInput(); return }
        await context.audioWorklet.addModule(`${base}vad/pcm-capture.worklet.js`)
        if (closed) { stopInput(); return }
        node = new AudioWorkletNode(context, 'retail-pcm-capture', {
          numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1,
        })
        source = context.createMediaStreamSource(stream)
        mute = context.createGain()
        mute.gain.value = 0
        source.connect(node)
        node.connect(mute)
        mute.connect(context.destination)
        processor.resume()
        node.port.onmessage = (event: MessageEvent<Float32Array>) => {
          if (closed) return
          if (++queued > 64) {
            --queued
            fail(new Error('Máy xử lý âm thanh không kịp. Bạn thử mở mic lại nhé.'))
            return
          }
          const frame = event.data
          pending = pending.then(async () => {
            if (!closed) {
              await processor.process(frame, handle)
              if (!closed && processor.speaking && callbacks.onSpeechPreview) {
                preview.frame(lastProbability, processor.audioBuffer, processor.speechRealStartFired)
              }
              ready?.()
              ready = undefined
            }
          }).catch(fail).finally(() => { --queued })
        }
        const firstFrame = new Promise<void>(resolve => { ready = resolve })
        await context.resume()
        // A running AudioContext alone does not mean input frames have arrived.
        await firstFrame
      } catch (error) {
        stopInput()
        throw error
      }
    },
    flush: async () => {
      if (closed) return
      // Finish queued original frames before submitting a duration-limited turn.
      await pending
      if (!closed) processor.endSegment(handle)
    },
    destroy: async () => {
      if (closed) return
      closed = true
      preview.reset()
      ready?.()
      ready = undefined
      stopInput()
      processor.pause(() => {})
      await pending
      // The shared weights remain warm; no stream/context survives destroy().
    },
  }
}
