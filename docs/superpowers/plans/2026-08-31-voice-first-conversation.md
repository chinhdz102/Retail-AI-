# Voice-first Conversation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the kiosk into a hands-free voice loop that automatically listens, transcribes spoken turns, asks Qwen, speaks the answer, and resumes listening in Vietnamese, English, or Chinese.

**Architecture:** Move microphone lifecycle into a tested `VoiceConversationController`. It uses browser SpeechRecognition when available and falls back to a MediaRecorder/Web Audio RMS detector when it is not. `App.vue` remains responsible for session/agent/TTS orchestration and delegates input state and transcripts to the controller.

**Tech Stack:** Vue 3 + TypeScript, Vitest/jsdom, browser SpeechRecognition, MediaRecorder, Web Audio API, existing FastAPI/Groq/ElevenLabs endpoints.

## Global Constraints

- The browser never receives Groq or ElevenLabs API keys; it calls the existing backend routes only.
- Supported locales remain `vi-VN`, `en-US`, and `zh-CN`.
- Recognition/recording must pause while Sky speaks and resume only after playback completes.
- Recorder segments stop after 800 ms of silence or 12 seconds maximum and are discarded when empty.
- Microphone tracks, audio contexts, timers, and object URLs must be released on stop/unmount.
- Text entry remains available as a secondary fallback and the existing single-screen layout stays responsive.

---

### Task 1: Voice controller contract and failing tests

**Files:**
- Create: `apps/kiosk-airi/src/voice-controller.ts`
- Create: `apps/kiosk-airi/src/voice-controller.test.ts`

**Interfaces:**
- `VoiceConversationController` accepts injected browser dependencies and callbacks so tests do not require a physical microphone.
- `start(): Promise<void>` chooses SpeechRecognition or recorder fallback.
- `pause(): void`, `resume(): Promise<void>`, `stop(): void`, `setLocale(locale: string): void`.
- Callbacks emit `{ mode: 'idle' | 'recognition' | 'recorder', phase: 'idle' | 'listening' | 'recording' }`, transcript text, and typed errors (`permission`, `unsupported`, `runtime`).

- [ ] **Step 1: Write the failing recognition-loop tests**

Add a fake recognition class with `start`, `stop`, and event properties. Assert that `start()` configures `vi-VN`, starts once, final results emit one trimmed transcript, and `pause()` prevents the `onend` handler from restarting it.

- [ ] **Step 2: Run the recognition tests to verify RED**

Run: `npm --prefix apps/kiosk-airi run test:run -- src/voice-controller.test.ts`

Expected: FAIL because `voice-controller.ts` does not exist.

- [ ] **Step 3: Add the failing recorder fallback test**

Inject `getUserMedia`, a fake `MediaRecorder`, and a fake analyser whose RMS samples cross the speech threshold and then stay silent. Assert that one WebM blob is emitted after the silence window and that the mode reports `recorder`.

- [ ] **Step 4: Run the recorder test to verify RED**

Run: `npm --prefix apps/kiosk-airi run test:run -- src/voice-controller.test.ts`

Expected: FAIL for the missing controller implementation, not for test setup errors.

---

### Task 2: Implement the voice controller

**Files:**
- Modify: `apps/kiosk-airi/src/voice-controller.ts`
- Modify: `apps/kiosk-airi/src/voice-controller.test.ts`

**Interfaces:**
- The controller owns recognition instance, MediaStream, MediaRecorder, AudioContext/analyser, RMS polling timer, generation token, and cleanup.

- [ ] **Step 1: Implement the minimal SpeechRecognition path**

Configure `continuous=true`, `interimResults=false`, `maxAlternatives=1`, and the supplied locale. Emit only final, trimmed transcript text; stop before invoking the transcript callback; restart after an unexpected `onend` while active.

- [ ] **Step 2: Run recognition tests GREEN**

Run: `npm --prefix apps/kiosk-airi run test:run -- src/voice-controller.test.ts`

Expected: recognition tests pass.

- [ ] **Step 3: Implement MediaRecorder/VAD fallback**

Request `{ audio: true }`, select `audio/webm;codecs=opus` when supported, calculate RMS from `AnalyserNode.getByteTimeDomainData`, start a segment over `0.035` RMS, stop after `800` ms below threshold or `12_000` ms total, emit the blob, and release the segment recorder while keeping the monitor ready for the next turn.

- [ ] **Step 4: Implement permission fallback and lifecycle cleanup**

On `not-allowed`/`service-not-allowed` or recorder permission rejection, emit a typed permission error and remain idle. `pause`/`stop` cancel timers, stop recognition/recorder, close audio nodes/contexts, stop every track, and ignore stale callbacks through the generation token.

- [ ] **Step 5: Run all controller tests GREEN**

Run: `npm --prefix apps/kiosk-airi run test:run -- src/voice-controller.test.ts`

Expected: all controller tests pass with no unhandled timer warnings.

---

### Task 3: Integrate voice-first behavior into the Vue kiosk

**Files:**
- Modify: `apps/kiosk-airi/src/App.vue`
- Modify: `apps/kiosk-airi/src/components/ChatPanel.vue`
- Modify: `apps/kiosk-airi/src/App.test.ts`

**Interfaces:**
- `App.vue` creates one controller after mount, passes the active locale, sends controller transcripts to `sendMessage`, and resumes the controller through the existing `speak` completion callback.
- `ChatPanel` exposes the active voice state and labels the primary mic action as start/stop voice conversation; the textarea remains a secondary fallback.

- [ ] **Step 1: Add failing app integration assertions**

Mock the controller/API and assert mount attempts voice start after the greeting, a recognition transcript appears as a user message and triggers `/agent/respond`, and `onSpeechEnd` calls `resume`.

- [ ] **Step 2: Run app tests to verify RED**

Run: `npm --prefix apps/kiosk-airi run test:run -- src/App.test.ts`

Expected: FAIL because the current app only owns the old manual recorder path.

- [ ] **Step 3: Replace inline microphone lifecycle with the controller**

Use controller callbacks to update `status`, `isRecording`, `isHandsFree`, `voiceHint`, and `errorMessage`. Call `pause` before `/agent/respond` and while TTS plays; call `resume` after audio or browser speech ends. The mic action starts/stops the controller rather than requiring typed input.

- [ ] **Step 4: Add permission CTA and localized voice copy**

Show a prominent labelled microphone action when idle/permission-denied, retain `aria-live="polite"`, keep touch targets at least 44px, and use the existing Vietnamese/English/Chinese language settings for all voice hints.

- [ ] **Step 5: Run app tests GREEN**

Run: `npm --prefix apps/kiosk-airi run test:run -- src/App.test.ts`

Expected: the voice-first integration assertions and existing text fallback tests pass.

---

### Task 4: Full verification and browser smoke test

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-31-voice-first-conversation-design.md`

- [ ] **Step 1: Run frontend tests and build**

Run: `npm --prefix apps/kiosk-airi run test:run` and `npm --prefix apps/kiosk-airi run build`.

Expected: all Vitest tests pass and Vite exits 0.

- [ ] **Step 2: Run backend regression checks**

Run: `uv run pytest -q` and `uv run ruff check .`.

Expected: 25+ backend tests pass and Ruff reports no errors.

- [ ] **Step 3: Browser-test the voice-first loop**

Open `http://127.0.0.1:5173`, grant microphone permission when supported, verify the greeting, active listening state, fake/injected recognition transcript, Qwen response, TTS playback, and automatic return to listening. Verify EN/ZH ElevenLabs audio and the documented Vietnamese Free-plan browser fallback.

- [ ] **Step 4: Document the real-device permission requirement**

Update README with the browser permission step and the fact that Playwright/headless environments may need the keyboard fallback because they have no physical microphone.
