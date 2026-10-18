import { describe, expect, it } from 'vitest'

import { listMicrophoneOptions, preferredMicrophoneId } from './microphone-devices'

describe('microphone device selection', () => {
  const devices = [
    { deviceId: 'default', groupId: 'amd', kind: 'audioinput', label: 'Default - Microphone Array (AMD Audio Device)' },
    { deviceId: 'desk-in', groupId: 'deskin', kind: 'audioinput', label: 'Microphone (DeskIn(R) Virtual Audio Device)' },
    { deviceId: 'amd-direct', groupId: 'amd', kind: 'audioinput', label: 'Microphone Array (AMD Audio Device)' },
    { deviceId: 'communications', groupId: 'amd', kind: 'audioinput', label: 'Communications - Microphone Array (AMD Audio Device)' },
    { deviceId: 'speaker', groupId: 'speaker', kind: 'audiooutput', label: 'Speakers' },
  ]

  it('hides browser default aliases and duplicate devices', () => {
    expect(listMicrophoneOptions(devices)).toEqual([
      { id: 'desk-in', groupId: 'deskin', label: 'Microphone (DeskIn(R) Virtual Audio Device)' },
      { id: 'amd-direct', groupId: 'amd', label: 'Microphone Array (AMD Audio Device)' },
    ])
  })

  it('defaults to the working AMD physical input and keeps an explicit selection', () => {
    const options = listMicrophoneOptions(devices)
    expect(preferredMicrophoneId(options)).toBe('amd-direct')
    expect(preferredMicrophoneId(options, 'desk-in', 'amd-direct')).toBe('desk-in')
  })

  it('falls back to the first physical input if the preferred mic is absent', () => {
    const options = listMicrophoneOptions(devices.slice(0, 2))
    expect(preferredMicrophoneId(options)).toBe('desk-in')
  })
})
