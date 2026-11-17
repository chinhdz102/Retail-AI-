/** Silence is a turn-taking hint; keep short thinking pauses and original PCM. */
export const SPEECH_ENDPOINT = {
  positiveSpeechThreshold: .5,
  negativeSpeechThreshold: .35,
  redemptionMs: 1440,
  preSpeechPadMs: 640,
  minSpeechMs: 160,
  submitUserSpeechOnPause: false,
} as const
