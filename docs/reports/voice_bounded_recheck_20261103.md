# Bounded voice endpoint recheck — 3 November 2026

The running demo accepted seven fixed uploads. All returned HTTP200 and
`whisper-large-v3`; no chat-model calls were made. Results are in
[the unique artifact](artifacts/voice_bounded_recheck_20261103T130646Z.json).

| Input | Result |
|---|---|
| FLEURS fixed first Vietnamese clip | Speech detected; WER0% |
| FLEURS fixed second Vietnamese clip | Speech detected; WER12.5% |
| ViMD11_0309 regional speech | Speech detected; WER33.3%, preserving the known incorrect recognition |
| Recorded ESC-50 keyboard control | Empty transcript, speech_detected=false |
| Seeded synthetic fan with pumping gain | Empty transcript, speech_detected=false |
| Seeded synthetic AC-like hum | Empty transcript, speech_detected=false |
| Seeded synthetic louder computer-fan-like noise | Empty transcript, speech_detected=false |

The ViMD reference includes “mô hình cây rong riềng”; Whisper still returns
“mô hình dòng diệt”. Raw diagnostics equal the final transcript and correction lists
are empty, locating this error before catalog correction. This is the same exposed
error documented earlier, not evidence of a newly introduced decoder regression.
No software change is justified by this replay alone. Do not hardcode its reference.

Azure VI ticket streaming returned HTTP200 with `vi-VN-HoaiMyNeural`. Its29,808-byte
MP3 decoded to4.968seconds; first audio including ticket creation took1,763ms. This
checks transport and decodability rather than pronunciation, perceived quality or
physical speaker behavior.

The focused speech-activity/text/Azure/tickets/lifecycle/noise suite passed94tests;
[XML result](artifacts/voice_bounded_scoped_20261103.xml). No production voice edits
were made during this check.

These are exposed development samples. Synthetic fan/AC signals do not represent
the user's physical room, microphone, browser AEC/AGC or computer fans. ESC category
labels have not been independently annotated for background speech. The replay is
an upload to the endpoint, not browser end-to-end capture. The three WER results
are individual diagnostic clips rather than a general accuracy estimate.

Existing paper guidance remains applicable: VAD detects speech presence without
verifying words; enhancement quality does not imply ASR improvement; Whisper
metadata is not calibrated word correctness. Previous paired denoiser results do
not support switching the default. LiveKit remains unintegrated. Acoustic human-level
quality has not been established; exact failing mic captures via the opt-in local
diagnostics remain the next necessary evidence.
