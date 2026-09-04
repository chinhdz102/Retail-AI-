# Voice-first Sky Conversation Design

## Goal

Make the kiosk a voice-first experience: after microphone permission is granted,
Sky continuously listens, sends spoken turns to Groq Whisper and Qwen, speaks the
answer, and resumes listening without requiring keyboard chat.

## User experience

1. The page creates a private session and speaks the Vietnamese greeting.
2. The app requests microphone access. If the browser requires a user gesture, the
   primary action is a large `Bật trò chuyện bằng giọng nói` button.
3. Once access is granted, the app enters `listening` and shows the active locale.
4. A spoken turn is detected, transcribed, sent to the existing agent endpoint, and
   added to the conversation as a transcript rather than a typed prompt.
5. Sky speaks the response. Recognition and recording are paused during playback,
   then resumed automatically after playback ends.
6. If recognition or recording fails, the user sees a specific recovery action. The
   text composer remains available as a secondary fallback, not the primary flow.

## Architecture

The existing backend contracts remain unchanged. A new client-side voice controller
owns microphone lifecycle and emits `listening`, `transcribing`, `thinking`, and
`speaking` transitions to `App.vue`.

- Primary input: browser `SpeechRecognition`/`webkitSpeechRecognition` in continuous
  mode, with `vi-VN`, `en-US`, or `zh-CN` selected from the language picker.
- Portable fallback: `getUserMedia` + `MediaRecorder` with a Web Audio analyser. The
  controller starts a segment after speech crosses an RMS threshold, stops after
  800 ms of silence, and hard-stops at 12 seconds. The resulting WebM/Opus blob is
  sent to `POST /audio/transcribe`.
- Output: existing `/audio/synthesize` first; browser `SpeechSynthesis` fallback on
  provider/format errors. Vietnamese continues to fall back on ElevenLabs Free
  accounts because the Giang Professional voice requires a paid plan.
- Echo prevention: stop recognition and analyser processing while an audio element or
  speech synthesis utterance is speaking. Resume only from the `onended`/`onend`
  callback and ignore stale callbacks using a conversation generation token.

## State and failure handling

The controller exposes `mode: 'recognition' | 'recorder' | 'idle'`, `permission:
'unknown' | 'granted' | 'denied'`, and callbacks for transcript/error. Permission
denial does not destroy the session; it changes the primary action to retry microphone
access and keeps text input usable. Empty transcripts are discarded without calling
the backend. A stopped conversation releases tracks, analyser nodes, timers, and
object URLs.

## Testing

- Unit-test the controller with a fake SpeechRecognition implementation: auto-start,
  final transcript emission, pause during speech, and restart after `onend`.
- Unit-test the recorder fallback with fake `getUserMedia`, `MediaRecorder`, and
  analyser samples: speech start, silence stop, and one `/audio/transcribe` call.
- Mount the Vue app and assert the voice-first permission CTA, language locale, and
  transcript-to-response loop. Keep one text-composer test as the documented fallback.
- Browser smoke test: grant microphone permission when supported, inject a fake
  recognition transcript when physical audio is unavailable, and verify the UI returns
  to listening after Qwen/TTS. Also verify real EN/ZH ElevenLabs audio and the
  documented Vietnamese Free-plan fallback.

## Accessibility and responsive behavior

The primary voice CTA and stop button are keyboard reachable, labelled, and at least
44px tall. `aria-live="polite"` announces listening/transcribing/speaking changes.
Reduced-motion users get the same states without decorative animation. The existing
single-screen layout and mobile breakpoint remain unchanged.
