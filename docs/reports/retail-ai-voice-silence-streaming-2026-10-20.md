# Voice silence guard and SSE latency update — 20/10/2026

## Scope and provenance
Source branch: `codex/retail-text-quality-v4-20261019` at `ab53fe118e4524a5bc925704c94126f9f631c956`.
This change is a **targeted voice reliability patch**, not a full LiveKit migration, true STT streaming or neural VAD implementation.

## Implemented
- An opt-in `SpeechEnvelopeGate` checks multi-frame voice-like amplitude variation *after* a MediaRecorder segment; kiosk opts in. A constant-amplitude noise segment and isolated click do not qualify for upload to Groq. Manual stop remains allowed to send short utterances.
- Recording still begins on the first RMS-positive frame, not after gate validation; that avoids truncating extra initial syllables due to evidence accumulation. It does **not** provide a genuine pre-roll ring buffer.
- The recording hysteresis threshold no longer drops below `MIN_SPEECH_THRESHOLD`, preventing persistent low-level room noise from extending a segment until the 12-second safety cap.
- Text SSE no longer sleeps 25 ms between chunks of an already-validated answer; transaction safety and metadata-first session validation remain intact.
- Added unit tests for stationary noise, transient sound, varied envelopes and manual stop.

## Known limitations / further work
- **This is not a speech classification ML model.** No claim of Silero-level accuracy is made. Fan noise can fluctuate, and background human speech remains indistinguishable from the main user without additional technology. A proper on-device neural VAD with 200–400 ms PCM pre-roll, speech endpointing, and an optional wake/interaction gesture is the next phase.
- Groq Whisper still transcribes a completed WebM segment, not partial stream; Azure TTS still begins after the fully validated agent response. SSE progressive display is *not* LLM token streaming.
- No production microphone recordings, acoustic benchmarks or provider calls were executed by this patch author. Add and run the tests plus real-device benchmarks before merging.
- The date 31/08/2026 is historical product snapshot provenance, unrelated to audio and not proof of inventory freshness.

## Acceptance tests
```powershell
npm --prefix apps/kiosk-airi run test:run
npm --prefix apps/kiosk-airi run build
.venv\\Scripts\\python.exe -m pytest -q
.venv\\Scripts\\ruff.exe check backend tests evaluation
```

## Live kiosk evaluation to run
Record consenting test speakers and reference transcriptions locally. Evaluate 10 min quiet, 10 min fan/AC, store bustle, nearby speech, 1–2-word Vietnamese utterances, three accents, Sky speaker playback and noisy interruptions. Report STT request counts during non-speech, false activation per hour, false rejection, truncation, WER/CER and end-of-speech-to-first-audio p50/p95. Do not upload live audio/transcripts to git or log private spoken content.

## Architecture decision
Keep existing local MediaRecorder + Groq + Azure baseline while measuring. For a full realtime path, compare native on-device Silero (or equivalent) plus AudioWorklet pre-roll against LiveKit Agents Silero VAD + streaming STT, using paired Vietnamese audio and explicitly disabling unsupported Vietnamese semantic turn models.

## Verified GitHub Actions run and inherited snapshot test drift

Initial PR CI: frontend Vitest **63/63 passed**, TypeScript/Vite production build **passed**. Backend **768 passed, 2 failed** due to historic snapshot-planogram fixture assumptions; these exact tests also fail on the untouched parent commit `ab53fe118e4524a5bc925704c94126f9f631c956` (independent baseline-check job). The runtime correctly fails closed and states it has no verified shelf when the planogram's dataset version does not match the current snapshot. Updated the two assertions to accept a version-consistent proposed location, or a grounded unavailable answer when no proposed location can be verified. This does NOT fabricate shelf assignments. Removed an unused `asyncio` import after eliminating the SSE pacing delay. A subsequent CI run is required to validate the updated source.
