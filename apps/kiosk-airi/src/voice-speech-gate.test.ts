import { describe, expect, it } from 'vitest'
import { SpeechEnvelopeGate } from './voice-speech-gate'

describe('SpeechEnvelopeGate (energy heuristics, not ML VAD)', () => {
  it('rejects silence and a one-frame click', () => {
    const gate = new SpeechEnvelopeGate()
    expect(gate.hasSpeechEvidence()).toBe(false)
    gate.observe(0.4, 40)
    expect(gate.hasSpeechEvidence()).toBe(false)
  })

  it('rejects sustained stationary noise regardless of its volume', () => {
    const gate = new SpeechEnvelopeGate()
    for (let i = 0; i < 300; i += 1) gate.observe(0.18, 40)
    expect(gate.hasSpeechEvidence()).toBe(false)
  })

  it('accepts a varied conversational speech envelope', () => {
    const gate = new SpeechEnvelopeGate()
    for (const level of [0.02, 0.035, 0.047, 0.022, 0.032, 0.051, 0.029]) {
      gate.observe(level, 40)
    }
    expect(gate.hasSpeechEvidence()).toBe(true)
  })

  it('can be reset between turns without leaking evidence', () => {
    const gate = new SpeechEnvelopeGate()
    for (const level of [0.02, 0.035, 0.047, 0.022, 0.032, 0.051, 0.029]) {
      gate.observe(level, 40)
    }
    expect(gate.hasSpeechEvidence()).toBe(true)
    gate.reset()
    expect(gate.hasSpeechEvidence()).toBe(false)
  })

  it('recognizes only sustained flat envelopes as stationary interference', () => {
    const gate = new SpeechEnvelopeGate()
    for (let i = 0; i < 8; i += 1) gate.observe(0.15, 40)
    expect(gate.stationaryNoiseLevel()).toBeNull()
    for (let i = 0; i < 8; i += 1) gate.observe(0.15, 40)
    expect(gate.stationaryNoiseLevel()).toBeCloseTo(0.15)
    gate.reset()
    for (let i = 0; i < 16; i += 1) gate.observe(i % 2 ? 0.06 : 0.15, 40)
    expect(gate.stationaryNoiseLevel()).toBeNull()
  })

  it('rejects non-finite samples without corrupting state', () => {
    const gate = new SpeechEnvelopeGate()
    gate.observe(Number.NaN, 40)
    gate.observe(0.05, Number.NaN)
    expect(gate.hasSpeechEvidence()).toBe(false)
  })
})
