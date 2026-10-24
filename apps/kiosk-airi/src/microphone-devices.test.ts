import { describe, expect, it } from 'vitest'

import { isLikelyVirtualMicrophone, listMicrophoneOptions, preferredMicrophoneId } from './microphone-devices'

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

  it('identifies common virtual audio inputs without flagging the AMD microphone', () => {
    expect(isLikelyVirtualMicrophone('Microphone (DeskIn(R) Virtual Audio Device)')).toBe(true)
    expect(isLikelyVirtualMicrophone('Microphone Array (AMD Audio Device)')).toBe(false)
  })

  it('defaults to the working AMD physical input instead of a previously saved virtual mic', () => {
    const options = listMicrophoneOptions(devices)
    expect(preferredMicrophoneId(options)).toBe('amd-direct')
    expect(preferredMicrophoneId(options, '', 'desk-in')).toBe('amd-direct')
    expect(preferredMicrophoneId(options, 'desk-in', 'desk-in')).toBe('amd-direct')
  })

  it('keeps a mic explicitly selected by the user', () => {
    const options = listMicrophoneOptions(devices)
    expect(preferredMicrophoneId(options, 'desk-in', 'desk-in', true)).toBe('desk-in')
    expect(preferredMicrophoneId(options, '', 'desk-in', true)).toBe('')
  })

  it('uses the OS default instead of auto-selecting a virtual input', () => {
    const options = listMicrophoneOptions(devices.slice(0, 2))
    expect(preferredMicrophoneId(options)).toBe('')
    expect(preferredMicrophoneId(options, '', 'desk-in')).toBe('')
    expect(preferredMicrophoneId(options, 'desk-in')).toBe('')
  })

  it('falls back to the first non-virtual input when the preferred mic is absent', () => {
    const options = listMicrophoneOptions([
      { deviceId: 'virtual', groupId: 'virtual', kind: 'audioinput', label: 'Virtual Audio Device' },
      { deviceId: 'usb', groupId: 'usb', kind: 'audioinput', label: 'USB Microphone' },
    ])
    expect(preferredMicrophoneId(options)).toBe('usb')
  })
})
