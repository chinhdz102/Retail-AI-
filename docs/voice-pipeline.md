# Kiosk voice pipeline

This note records the STT/TTS patterns adapted from [ParrotGo](https://github.com/nvuoc/parrotgo)
for the retail kiosk. Only voice infrastructure concepts were reused; its booking and
dispatch workflows are outside this project.

## Speech to text

1. The visitor explicitly starts microphone capture. The browser requests echo cancellation,
   noise suppression, and automatic gain control, then records WebM/Opus where supported.
2. A Web Audio RMS detector estimates the quiet-room noise floor while no utterance is active.
   It adjusts the speech threshold within fixed limits and uses a lower threshold after speech
   begins so quiet syllables do not split the turn.
3. Segments end after 600 ms of silence or at 12 seconds. Segments with less than 120 ms of
   detected voice are discarded as likely clicks or handling noise. A manual stop flushes the
   current segment even when it is shorter.
4. The browser uploads the segment to `POST /audio/transcribe`. The backend calls Groq Whisper
   `whisper-large-v3-turbo` first and falls back to `whisper-large-v3` on provider failure.
   Retail vocabulary hints and the selected language are sent with the transcription request.

## Text to speech

1. The kiosk posts the session, reply text, and language to `POST /audio/synthesize/tickets`.
   The API stores the text in a bounded in-memory ticket store for 60 seconds and returns a
   random, one-use stream URL. The text itself is never placed in the URL.
2. The audio element requests that URL. FastAPI opens the configured Azure Speech stream (or
   ElevenLabs when Azure is not configured), prefetches the first non-empty audio chunk, then
   forwards subsequent chunks as they arrive. Cancelling playback closes the upstream response.
3. Logs record provider, model, time to first audio, total duration, and completion state;
   they do not record the spoken text. The frontend falls back to browser SpeechSynthesis if
   cloud synthesis or playback fails.

The ticket store is intentionally bounded and one-use for the local kiosk deployment. A
multi-process deployment would need a shared ticket store or signed, short-lived tickets so a
request can be consumed by any API worker.

## Kiosk feedback and recovery

The microphone must be started explicitly with **Bắt đầu nói**. A visible state and input
level show whether capture is active. Browser AudioContext is resumed before monitoring;
the input selector lists available microphones and shows the actual capture device label.
microphone setup reports a visible error after 12 seconds instead of waiting indefinitely.
The 12-second segment limit also applies to uninterrupted speech.

Hands-free intent remains enabled while STT, the agent, and TTS run, so capture can resume
after playback. Transcription errors remain visible when capture resumes. Stopping voice
mode cancels playback and prevents its old completion callback from restarting capture.

API failures retain their HTTP status. If the in-memory backend session expires or is lost
after a server restart (404/410 session error), the frontend creates one replacement session
and retries the current request once. Other API failures are displayed, with a 45-second
request timeout. A replacement session cannot recover the backend's lost conversation history.

The mobile transcript has a bounded scroll area above the composer, with a compact header.
Suggested prompts disappear after the first message. New transcripts and replies scroll
into view without covering the avatar.

The live service check is recorded in `evaluation/results/voice_repair_20261018.json`.
Its three inputs are Azure-generated Vietnamese speech sent through the real Groq STT,
agent, and TTS endpoints; this is not a microphone accuracy benchmark. One fallback LLM
turn took about 25 seconds, so provider fallback latency remains a limitation.

## Validation

The transferable ideas are explicit turn ownership, noise-aware turn detection, bounded
segments, cancellation-safe stream cleanup, one-use tickets, and time-to-first-audio metrics.
The kiosk keeps its existing MediaRecorder/Groq and Azure/ElevenLabs providers rather than
adding LiveKit as another runtime. Static phrase caches and filler-audio prefetch are not used
because kiosk replies are dynamic and there is no approved set of fixed phrases to play.

```powershell
uv run pytest tests/test_azure_speech_services.py tests/test_elevenlabs_services.py tests/test_speech_tickets.py tests/test_retail_api.py -q
npm --prefix apps/kiosk-airi run test:run
npm --prefix apps/kiosk-airi run build
```
