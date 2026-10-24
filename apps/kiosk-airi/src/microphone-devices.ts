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

export function isLikelyVirtualMicrophone(label: string): boolean {
  return /(?:virtual|deskin|cable input|stereo mix|loopback|voicemeeter)/i.test(label)
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
  keepCurrentSelection = false,
): string {
  if (keepCurrentSelection && !currentId) return ''
  if (keepCurrentSelection && devices.some((device) => device.id === currentId)) return currentId

  const amdArray = devices.find((device) =>
    normalizedLabel(device.label) === 'microphone array (amd audio device)',
  )
  if (amdArray) return amdArray.id
  const savedPhysicalDevice = devices.find((device) => device.id === savedId && !isLikelyVirtualMicrophone(device.label))
  if (savedPhysicalDevice) return savedPhysicalDevice.id
  const currentPhysicalDevice = devices.find((device) => device.id === currentId && !isLikelyVirtualMicrophone(device.label))
  if (currentPhysicalDevice) return currentPhysicalDevice.id
  const firstPhysicalDevice = devices.find((device) => !isLikelyVirtualMicrophone(device.label))
  // If the browser exposes only a virtual input, let getUserMedia use the OS
  // default instead of pinning capture to a loopback device such as DeskIn.
  return firstPhysicalDevice?.id ?? ''
}
