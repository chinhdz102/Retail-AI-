export interface MicrophoneOption {
  id: string
  label: string
  groupId: string
}

export interface AudioInputDeviceLike {
  deviceId: string
  groupId?: string
  kind: string
  label: string
}

function normalizedLabel(label: string): string {
  return label
    .trim()
    .replace(/^(?:default|communications)\s*-\s*/i, '')
    .replace(/\s+/g, ' ')
    .toLocaleLowerCase()
}

export function listMicrophoneOptions(devices: AudioInputDeviceLike[]): MicrophoneOption[] {
  const inputs = devices.filter((device) => device.kind === 'audioinput' && device.deviceId)
  const physicalInputs = inputs.filter((device) => !['default', 'communications'].includes(device.deviceId))
  const candidates = physicalInputs.length ? physicalInputs : inputs
  const seen = new Set<string>()

  return candidates.flatMap((device, index) => {
    const label = device.label.trim() || `Micro ${index + 1}`
    const key = device.groupId || normalizedLabel(label)
    if (seen.has(key)) return []
    seen.add(key)
    return [{ id: device.deviceId, label, groupId: device.groupId ?? '' }]
  })
}

export function preferredMicrophoneId(
  devices: MicrophoneOption[],
  currentId = '',
  savedId = '',
): string {
  if (currentId && devices.some((device) => device.id === currentId)) return currentId
  if (savedId && devices.some((device) => device.id === savedId)) return savedId

  const amdArray = devices.find((device) =>
    normalizedLabel(device.label) === 'microphone array (amd audio device)',
  )
  return amdArray?.id ?? devices[0]?.id ?? ''
}
