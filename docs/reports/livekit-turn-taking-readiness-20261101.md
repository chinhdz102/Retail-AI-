# LiveKit turn-taking readiness

**Status (1 November 2026): research only.** The kiosk does not install the
LiveKit Agents runtime, LiveKit transport, or a LiveKit turn detector. This note
maps the official turn-handling guidance to the implementation that is actually
in the repository. It is a design and measurement checklist, not evidence that
LiveKit is running in the kiosk.

## What the official guidance says

The [LiveKit turn handling guide](https://docs.livekit.io/agents/logic/turns/)
separates four decisions:

- **Turn detection:** VAD supplies speech boundaries; endpointing decides when
  a thought is complete. A semantic turn detector combines acoustic and meaning
  signals. VAD-only is the documented choice when minimum latency matters or the
  semantic detector does not support the target language. STT endpointing and
  manual turn control are other explicit modes.
- **Endpointing:** fixed minimum/maximum delay (or Python dynamic endpointing)
  trades response latency against cutting off a pause inside a sentence.
- **Interruption:** `vad` treats speech start/stop as a barge-in signal;
  `adaptive` gates that signal shortly after agent speech starts and uses an
  adaptive detector to filter conversational backchannels. `min_duration` and
  `min_words` further gate interruptions. Realtime models can own these signals
  on the server, so client settings do not have the same effect.
- **False interruptions:** if VAD interrupts but STT produces no user words, the
  event is considered a false interruption. With a configured timeout (the
  Python example uses 2 seconds) and `resume_false_interruption`, the agent can
  resume the prior speech. This is distinct from a real user turn, which should
  cancel the prior response.

The guide also recommends noise cancellation for turn detection and STT. It does
not establish Vietnamese support for a particular semantic detector; the model's
language list must be checked before selecting that mode.

## Research context

The LiveKit settings are engineering controls, not a substitute for a labeled
turn-taking evaluation. Relevant research frames the same problem in terms of
predicting end-of-turn intent and handling overlap:

- [Skantze, *Towards a General, Continuous Model of Turn-taking in Spoken Dialogue using LSTM Recurrent Neural Networks* (2017)](https://doi.org/10.18653/v1/W17-5527) models turn-taking continuously rather than treating silence as a binary boundary.
- [Ekstedt and Skantze, *Neural Turn-Taking Models for Spoken Dialogue Systems* (2021)](http://hdl.handle.net/2262/96251) predicts whether the speaker will continue, yield, or take the turn; it motivates evaluating pause timing and overlap separately.
- [Ferrer et al., *Improving End-of-Turn Detection in Spoken Dialogues by Detecting Speaker Intentions as a Secondary Task* (2018)](https://doi.org/10.1109/ICASSP.2018.8461997) shows why acoustic endpointing alone can benefit from an intent signal.

These papers use research datasets and do not establish accuracy for Vietnamese
retail speech, kiosk acoustics, or LiveKit's current detector. They support the
measurement plan above: label continuation, yield, overlap, backchannel, and
false interruption separately instead of reporting one endpoint accuracy number.

## Current kiosk mapping

| Concern | Current behavior | LiveKit-level gap |
| --- | --- | --- |
| Speech activity | Local Silero VAD v5 in ONNX Runtime Web; browser requests echo cancellation, noise suppression, and AGC. | No room/participant transport or server VAD. Acoustic echo still needs a device/room test. |
| Endpointing | 600 ms pre-speech padding, 250 ms minimum speech, 900 ms redemption, 12 s segment cap. Groq Whisper runs after a complete segment. | No semantic endpointing, dynamic pause adaptation, or partial STT during the turn. |
| Barge-in | `onSpeechRealStart` changes state; `App.vue` immediately stops cloud/browser TTS. | No adaptive backchannel filter; any confirmed local speech onset is treated as an interruption. |
| False interruption | Empty/non-speech/repeated transcript is rejected; an interrupted reply is retained for 15 s and replayed from a fresh ticket, then hands-free resumes. | No resume-from-offset, adaptive backchannel filter, or LiveKit runtime. |
| Lifecycle safety | Generation tokens serialize pause/resume and drop callbacks from old microphones. | The equivalent needs to remain a hard invariant in any migration. |
| Transport | HTTP WAV upload, SSE answer text, one-use ticket with chunked Azure/ElevenLabs audio. | No LiveKit room, participant identity, audio track, agent session, or LiveKit observability. |

The relevant source points are `apps/kiosk-airi/src/voice-controller.ts` (VAD
and generation ownership) and `apps/kiosk-airi/src/App.vue`
(`interruptSpeechForBargeIn`, `handleVoiceAudio`, and `speak`). Existing voice
research and audit reports record the same boundary and explicitly mark LiveKit
as unimplemented.

## Safe next step before any migration

Collect a consented, local Vietnamese kiosk set with human labels for speech start,
speech end, sentence-internal pauses, backchannels (for example “ừ”, “à”), true
barge-ins, and silence/noise. Run it on the actual microphone and speaker layout.
Record at least:

1. endpoint delay p50/p95 and truncation rate;
2. false interruption rate per hour and the fraction that produces empty STT;
3. true barge-in detection and time-to-playback-stop;
4. STT request count for silence, noise, and backchannels;
5. transcript WER/CER by language/accent and segment length;
6. first-audio and full-response latency, including cold provider requests.

Use this baseline to compare the current browser path with a separate LiveKit
prototype. Do not replace Groq/Azure or claim semantic turn detection until the
prototype has equivalent Vietnamese and device evidence.

## Candidate migration contract

If a LiveKit prototype is approved, preserve these invariants:

- Keep session ownership, catalog grounding, policy checks, and payment state in
  the existing API; a room or agent must not become a second business source of
  truth.
- Carry a monotonic conversation/turn ID through VAD, STT, agent, and TTS events;
  late callbacks must be ignored after interruption, stop, language change, or
  session reset.
- Start with VAD-only or manual turn control unless the selected semantic model's
  language support is verified. Treat adaptive interruption as an experiment with
  explicit thresholds and labeled backchannels.
- Prototype false-interruption resume behind a feature flag. Retain the canceled
  response only with its turn ID, language, and an expiry (for example 2 seconds);
  replay it only when STT is empty and no newer customer turn exists. A real user
  transcript must permanently supersede the old response.
- Keep audio and transcript privacy controls: do not log customer audio or spoken
  text by default, and measure provider/transport timings separately from content.

The current kiosk can continue shipping its local VAD and HTTP pipeline while this
evidence is collected. A LiveKit dependency should be added only with a concrete
latency, interruption, or transport requirement that the baseline cannot meet.

## Sources

- [LiveKit turn detection and interruptions](https://docs.livekit.io/agents/logic/turns/)
- [Silero VAD](https://github.com/snakers4/silero-vad)
- [vad-web API (`onSpeechRealStart` and misfire handling)](https://docs.vad.ricky0123.com/user-guide/api/)
- [`docs/voice-pipeline.md`](../voice-pipeline.md)
- [`docs/reports/consultation_voice_research_20261101.md`](consultation_voice_research_20261101.md)
- [`docs/reports/consultation_voice_audit_20261101.md`](consultation_voice_audit_20261101.md)
