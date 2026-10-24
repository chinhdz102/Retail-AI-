# Kiosk voice pipeline

This note records the STT/TTS patterns adapted from [ParrotGo](https://github.com/nvuoc/parrotgo)
for the retail kiosk. Only voice infrastructure concepts were reused; its booking and
dispatch workflows are outside this project.

## Speech to text

1. The visitor starts microphone capture explicitly. Silero VAD v5 runs locally in the browser
   through ONNX Runtime Web; microphone capture requests echo cancellation, noise suppression,
   and automatic gain control. The VAD keeps 800 ms of pre-speech audio, requires 400 ms of speech,
   and waits for 1,400 ms of silence before ending a turn (with a 30-second hard limit). These
   settings preserve initial consonants and keep ordinary pauses between Vietnamese clauses inside
   one turn.
2. The VAD returns only speech segments. The browser converts them to 16 kHz, mono, PCM WAV and
   stops the microphone before uploading, which avoids recording the kiosk's own spoken reply.
   The model, worklet, and WASM runtime are served from the app itself instead of a CDN.
   The backend rejects near-silent PCM, sustained sine-like PCM, and steady unvoiced noise before
   sending it to Whisper. For quiet PCM (at or below 0.03 RMS and 0.15 peak), and for louder PCM
   with a nearly constant 100 ms energy envelope (coefficient of variation at or below 0.15), it
   checks 40 ms frames every 100 ms for periodic energy in the human voice range. It rejects only
   when both mean periodicity is below 0.58 and the voiced-frame ratio is below 0.15. This extends
   the earlier quiet-noise check to steady, louder room hum while retaining signals with voiced
   harmonics. The check is a safety filter, not a general acoustic speech classifier; changing
   background speech, music, or the wrong microphone can still require a device/room fix.
3. The backend calls Groq Whisper `whisper-large-v3` first and falls back to
   `whisper-large-v3-turbo` when the first model request fails. It requests
   `verbose_json` at temperature 0 and uses a short, neutral Vietnamese retail context so the prompt
   does not over-bias recognition toward a fixed list of product words.
4. The backend rejects a candidate when at least half of its returned segment duration is marked
   non-speech (`no_speech_prob >= 0.5`), at least half of the speech duration has a compression
   ratio of 2.4 or higher, or the speech-weighted `avg_logprob` is `<= -0.75`. These conservative
   checks are applied to the primary model. If it rejects a candidate as low quality or non-speech,
   the backend stops instead of letting the fallback model invent a transcript. The transcript is
   empty and never reaches the retail agent; the UI asks the customer to repeat and resumes
   hands-free listening. The fallback remains available for provider and request failures. The API
   returns `speech_detected` and quality metrics for diagnostics.
5. Groq requests segment granularity explicitly and the backend requires the documented verbose
   segment quality fields before it accepts text. It also rejects a long repeated phrase loop when
   the same four-or-more-token sequence accounts for at least 35% of the transcript. The kiosk
   repeats this loop check as a second guard before sending a transcript to the retail agent. A
   rejected loop returns no customer message and asks the visitor to repeat; ordinary word-level
   repetition such as “mua một chai, mua thêm hai chai” remains accepted.
6. VAD callbacks carry the microphone generation that created them. Pause, resume, start, and
   segment cleanup are serialized, and a new generation gets a fresh VAD instance. A late event from
   a paused microphone cannot be mistaken for the current visitor's words.

## Text to speech

1. The kiosk posts the session, reply text, and language to `POST /audio/synthesize/tickets`.
   The API stores the text in a bounded in-memory ticket store for 60 seconds and returns a
   random, one-use stream URL. The text itself is never placed in the URL.
2. The audio element requests that URL. FastAPI opens the configured Azure Speech stream (or
   ElevenLabs when Azure is not configured), waits for the first non-empty audio chunk, then
   forwards it and subsequent chunks as they arrive. Cancelling playback closes the upstream
   response. This already uses the same streaming/cancellation pattern as ParrotGo.
3. Logs record provider, model, time to first audio, total duration, and completion state;
   they do not record the spoken text. The frontend falls back to browser SpeechSynthesis if
   cloud synthesis or playback fails.

The ticket store is intentionally bounded and one-use for the local kiosk deployment. A
multi-process deployment would need a shared ticket store or signed, short-lived tickets so a
request can be consumed by any API worker.

## Kiosk feedback and recovery

The microphone must be started explicitly with **Bắt đầu nói**. A visible state and input
level show whether capture is active. The input selector lists available microphones and
shows the actual capture device label. An explicitly chosen microphone is restored before the first
request. Legacy saved IDs are hints; when available, the physical `Microphone Array (AMD Audio
Device)` is preferred over a stale virtual input. If a chosen device ID is stale, capture retries
once with the OS default. When the level stays near zero, the kiosk now says that the selected input
has almost no signal instead of implying Whisper failed.
Silero and microphone setup report a visible error after 12 seconds instead of waiting indefinitely.
A continuous utterance is capped at 30 seconds.

After a report of clipped/incorrect live microphone transcripts, the capture settings were compared
with the installed Silero VAD defaults. The previous 450 ms pre-roll and 1,050 ms endpoint could
drop initial consonants or split a sentence around a normal pause; they are now 800 ms and 1,400 ms,
with a 400 ms minimum segment. The kiosk also restores the saved physical microphone before the
first capture and retries the OS default if that device ID has gone stale. These changes target
capture and segmentation; they do not prove accuracy on the user's room/microphone. The existing
16-case STT set uses generated Azure speech, and the provided long video has no complete verbatim
reference, so neither is a substitute for testing a real microphone recording.

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

The 21 October 2026 exact-reference benchmark used 16 Azure-generated Vietnamese utterances across
shopping, product, brand, follow-up, and colloquial speech cases. The latest rerun accepted all 16;
15 were exact after catalog-grounded corrections (mean WER 1.56%, mean CER 0.57%, median Groq STT
latency 511 ms, p95 1,867 ms). Raw Whisper output was exact in 10/16 cases; catalog-grounded
correction improved this to 15/16. The sole miss is “Sunsilk”, which is not in the active catalog
and was therefore left as Whisper returned it instead of guessing. Full per-case timings, model
metadata, and transcripts are in `evaluation/results/live_groq_stt_user_turn_20261021.json`.
This benchmark does not represent a live human microphone or every Vietnamese accent. A live
silence/noise probe initially reproduced a confident “Hãy subscribe cho
kênh La La School…” transcript from room-noise-only PCM even though the model's segment confidence
looked good. After the acoustic guard, continuous room noise, VAD-shaped 800 ms and 1.5 s noise
segments, and room noise at 8× amplitude were rejected before Whisper; low-volume Azure-generated
speech at 2% amplitude, both clean and mixed with room noise, remained correctly transcribed.
Digital silence and quiet/normal 440 Hz tones also return an empty transcript without a Whisper
request. A UI regression confirms that provider-marked non-speech text never reaches the retail
agent. On the user-provided 47.69-second video clip,
`whisper-large-v3` returned a full, readable transcript in 6.73 seconds and
`whisper-large-v3-turbo` returned one in 6.37 seconds. The accompanying reference was a summary,
not a verbatim transcript, so its WER/CER are invalid and are not used as accuracy scores. Azure TTS
synthesis and stream-first-byte latency are measured separately by
`evaluation/live_azure_tts_benchmark.py`. The latest three live trials all completed synthesis and
streaming: synthesis median/p95 was 230/1,180 ms, first chunk 147/157 ms, and stream completion
256/261 ms. The report is in
`evaluation/results/live_azure_tts_benchmark_20261021_noiseguard_final.json`. A separate live kiosk
API round trip transcribed the Azure-generated question “Có Coca Zero không ạ?” exactly in 699 ms,
then streamed the Azure reply through a one-use ticket; first audio arrived in 562 ms (one end-to-end
 trial, including network and API overhead).

The follow-up live check after the transcript guard used the provided 47.69-second Vietnamese
retail video. Both Groq models returned complete readable text with all three quality fields:
`whisper-large-v3` took 3,299 ms and `whisper-large-v3-turbo` took 1,448 ms. Both still confused
some ordinary words; the supplied transcript was a summary rather than a verbatim reference, so no
WER/CER claim is made. The exact repeated-loop output reported by the visitor was rejected in a
regression test even when mock Whisper metadata was high-confidence. Results are saved in
`evaluation/results/live_groq_stt_user_short_clip_20261021.json`.

The same-day Azure TTS streaming check completed 2/2 trials. Synthesis median/p95 was 867/1,531 ms;
the first streamed audio chunk arrived in 220/221 ms, and stream completion took 305/306 ms. This
is a two-trial latency sample, not a stable production percentile; details are in
`evaluation/results/live_azure_tts_parrotgo_recheck_20261021.json`.

The final follow-up on 21 October used the same 47.69-second Vietnamese clip through the complete
`POST /audio/transcribe` route. It returned a 1,000-character transcript in 5,253 ms with
`whisper-large-v3`; the segment quality metadata showed low no-speech probability and healthy
compression. The wording was readable, but Whisper still made a few ordinary word errors, so this
is a latency and end-to-end check, not a word-error score. Profiling found catalog correction had
taken 19,796 ms before indexing taxonomy phrases; after the index change, the same transcript took
382 ms to check against the catalog.
The correction now limits candidates using indexed category phrases and length-bucketed category
words; it still leaves unverified words unchanged. The 45 targeted catalog/STT tests pass.

The latest three-trial Azure Vietnamese TTS check completed synthesis and streaming in all trials.
First audio arrived in a median 190 ms (p95 228 ms); full streamed playback data completed in a
median 302 ms (p95 315 ms). Synthesis median was 307 ms, with a 1,165 ms first-trial cold-start
outlier. See `evaluation/results/live_azure_tts_user_turn_20261021.json`. This sample is small and
network-dependent. Groq Whisper remains file/segment based: the kiosk submits the segment after
Silero detects the end of speech, so it cannot show recognized words while the user is still
speaking. Azure TTS already sends audio chunks as they arrive and cancels upstream synthesis when
playback is stopped.

## Live recheck after the reported bad transcript

The Vietnamese retail prompt now names common package units (`chai`, `lon`, `hộp`, `gói`, `túi`,
`lốc`, `thùng`, `ml`, `lít`) while retaining the instruction to transcribe only what was heard. A
context-limited post-correction maps `trai` to `chai` only when it immediately precedes a measured
package size; it does not rewrite ordinary uses of `trai`. The real kiosk API corrected the live
Azure-generated question “Coca Zero trai 600ml giá bao nhiêu?” to “Coca Zero chai 600ml giá bao
nhiêu?” and the catalog returned the 600 ml Coca-Cola Zero item at 9,900đ.

The 21 October rerun used 16 exact-reference Azure-generated Vietnamese retail utterances. All 16
were accepted; 15 were exact after catalog correction (mean WER 1.56%, mean CER 0.57%). Median STT
latency was 546 ms and p95 was 1,752 ms. The only remaining miss is “Sunsilk”, absent from the
active catalog, so the system leaves Whisper's unverified spelling unchanged. Raw Whisper was exact
in 11/16; correction raised this to 15/16.
Per-case results are in `evaluation/results/live_groq_stt_catalog_benchmark_20261021_user_recheck.json`.

On the same 16 generated utterances, `whisper-large-v3` was exact after catalog correction in 15/16
cases, while `whisper-large-v3-turbo` was exact in 9/16 (mean WER 7.25%, median latency 371 ms).
The kiosk therefore keeps large-v3 as the primary model for the current accuracy-focused flow and
turbo as the provider-failure fallback; it does not switch models just because one transcript looks
uncertain, which could turn silence or weak audio into invented words.
The turbo measurements are in `evaluation/results/live_groq_stt_turbo_rerun_20261021.json`.

The exact repeated transcript reported by the visitor was synthesized and sent through the active
kiosk API. It returned an empty transcript with `speech_detected=false`. A separate two-second silent
WAV was rejected the same way; frontend regression tests confirm that neither a provider-marked
non-speech transcript nor a repeated loop reaches the agent. The live demo is compiled to use API
port 8001, which was used for these checks.

Azure Vietnamese TTS was rechecked in five synthesis and streaming trials: 5/5 completed, synthesis
median/p95 252/1,747 ms, first streamed chunk median/p95 154/736 ms, and stream completion
median/p95 257/1,028 ms. The first end-to-end ticket request had a 1,662 ms first chunk; the next
three live ticket streams had a 176 ms median first chunk and 264 ms median completion. These
samples show streaming is working, with occasional first-request/provider latency. Results are in
`evaluation/results/live_azure_tts_benchmark_20261021_voicefix.json`.

The new `/audio/synthesize/prewarm` route starts an Azure-key-authenticated voices-list request when the
mic enters listening/recording, so DNS/TLS setup overlaps the user's turn. The frontend sends only
the session ID and language; no transcript, response text, or audio is sent, and prewarming is
best-effort. On the live demo, the cold first audio chunk took 1,009 ms; preconnect took 1,361 ms in
the background, and five subsequent streamed replies returned their first chunk in 142–469 ms
(median 407 ms). The measurement is recorded in this document; voice playback remains incremental.

These accuracy cases use generated speech, not a human microphone recording. When the demo was
inspected idle, its mic meter read 0.000557 and showed the near-zero-input hint; that idle reading
does not measure a spoken utterance. A live recording with the visitor speaking is still needed to
separate room/microphone capture quality from Whisper quality.

## Validation

The transferable ideas are an on-device Silero VAD, explicit turn ownership, pre-speech padding,
bounded segments, cancellation-safe stream cleanup, one-use tickets, and time-to-first-audio
metrics. The kiosk keeps Groq Whisper and Azure/ElevenLabs; it does not add ParrotGo's LiveKit
runtime or Azure STT. Static phrase caches and filler prefetch are not used because kiosk replies
are dynamic and there is no approved fixed phrase set.

```powershell
uv run pytest tests/test_azure_speech_services.py tests/test_elevenlabs_services.py tests/test_speech_tickets.py tests/test_retail_api.py -q
npm --prefix apps/kiosk-airi run test:run
npm --prefix apps/kiosk-airi run build
```

## Public Vietnamese speech and live API noise check — 21 October 2026

To check conversational speech without recording the visitor, 24 short clips were downloaded from
[VietSuperSpeech](https://huggingface.co/datasets/thanhnew2001/VietSuperSpeech), one per source video,
and sent through `GroqService`. The dataset transcripts are generated by Zipformer, not verified by
humans, so the following WER/CER values measure agreement with noisy pseudo-labels rather than gold
accuracy.

| Model | Accepted | Pseudo-reference WER / CER | Median / p95 latency |
| --- | ---: | ---: | ---: |
| `whisper-large-v3` | 24/24 | 14.93% / 11.76% | 972 / 2,100 ms |
| `whisper-large-v3-turbo` | 24/24 | 14.64% / 11.26% | 848 / 1,541 ms |

Each run had three primary-key rate-limit retries that succeeded with the next configured key;
there were no failed clips. The small score difference is not a reliable reason to change the model:
on the same 12 human-transcribed FLEURS read-speech clips, large-v3 had 7.42% WER versus 7.77% for
turbo, and the separate turbo run was slower during the rate-limited session. On 16 exact-reference
retail utterances, catalog-corrected large-v3 was exact in 15/16 cases; turbo was exact in 9/16.
Large-v3 remains primary, with turbo reserved for provider fallback.

The conversational set also shows why Whisper confidence fields cannot prove a transcript is true:
large-v3 accepted one short, fluent answer with 100% WER against the dataset label, while turbo
recognized that clip much closer to the label. The dataset label itself may be wrong. The quality
gate is useful for silence and non-speech; it does not guarantee semantic correctness, so product
answers must still be grounded in catalog data and uncertain speech may need repetition.

The running `POST /audio/transcribe` endpoint was also sent four synthetic non-speech WAVs. Digital
silence (40.6 ms), low steady noise (146.2 ms), a 440 Hz tone (129.9 ms), and one 10 ms click (30 ms)
all returned `speech_detected=false` and an empty transcript before Whisper. A 14.8-second public
Vietnamese speech clip was accepted through the same endpoint in 1,922 ms. These are API/audio-file
checks, not a physical microphone recording. The browser session previously exposed only the DeskIn
virtual input; the AMD microphone was unavailable to that tab.

Validation for this recheck: `tests/test_groq_services.py` **42 passed**; the targeted STT/API pytest
selection **31 passed**; frontend voice-controller, app voice flow, repeated-transcript, and
microphone-device tests **30 passed**; Ruff and `git diff --check` passed. Details are in
`docs/reports/retail_ai_voice_reliability_20261021.md` and the raw results
`evaluation/results/live_groq_stt_vietsuperspeech_vi_filtered_20261021.json`,
`evaluation/results/live_groq_stt_fleurs_turbo_recheck_20261021.json`, and
`evaluation/results/live_groq_stt_api_noiseguard_20261021.json`.
