# Vietnamese ASR research addendum — 2026-11-03

## Scope and prior evidence

Read with [STT regression](stt_regression_20261103.md) and [noise filtering](noise_filtering_20261103.md). Their small, development-exposed samples are diagnostics, not a blind kiosk benchmark. The screenshot has a correct transcript: its apple-routing defect belongs to application interpretation and is being fixed separately. Changing ASR cannot be credited for fixing that routing failure.

## Deployment candidates

| Candidate | Primary evidence | Deployment and constraints |
|---|---|---|
| PhoWhisper small/large | [VinAI repository](https://github.com/VinAIResearch/PhoWhisper), [2024 paper](https://arxiv.org/abs/2406.02555), [license](https://github.com/VinAIResearch/PhoWhisper/blob/main/LICENSE) | Vietnamese Whisper fine-tunes with multiple model sizes and regional training coverage. BSD-3-Clause. Start with small as an inexpensive local shadow; large is a GPU-oriented comparator. Transformers inference is available; CTranslate2 conversion is an engineering option requiring transcript parity checks. No measured local latency or memory claim is made here. |
| PhoASR-whisper-small | [2026 EACL paper](https://aclanthology.org/2026.findings-eacl.345/), [official code](https://github.com/Qualcomm-AI-Research/PhoASR), [checkpoint card](https://huggingface.co/Qualcomm-AI-Research/PhoASR-whisper-small) | Self-hosted Whisper-small with Transformers example (30-second chunks, word timestamps, vi). Released card describes a 3,000-hour fine-tune, whereas the paper abstract describes a 500-hour unified dataset: pin revision and distinguish artifacts. Card lists BSD-3-Clause-Clear AND Qualcomm responsible-AI terms and research/educational intended use. Review exact model terms before commercial shipment; repository code license alone does not clear checkpoint use. |
| NVIDIA Parakeet-CTC-0.6B-Vietnamese | [Official model card](https://huggingface.co/nvidia/parakeet-ctc-0.6b-Vietnamese), [NIM deployment guide](https://docs.nvidia.com/nim/speech/latest/asr/deploy-asr-models/parakeet-ctc.html) | Independent 600M FastConformer/CTC model, NeMo load/transcribe path, mono input. Current HF card lists NVIDIA Open Model License and says commercial/noncommercial use is supported. NIM/API terms are separate. Useful GPU server comparator; measure CPU viability rather than assuming it. Pin revision because current card is Vietnamese–English code-switching while older NIM descriptions differ. |
| Groq Whisper large-v3 / turbo | [Official API docs](https://console.groq.com/docs/speech-to-text) | Retain current baseline. API supplies language/prompt and segment metadata. It does not document access to decoder hidden states, full logits, token posterior distributions or beams. A hidden-state method cannot simply be plugged into this hosted endpoint. |

Practical order: benchmark PhoWhisper-small first, PhoASR-small after terms review, then Parakeet for independent CTC errors and latency. No new model was downloaded or benchmarked in this addendum; none is declared superior to current Groq runtime.

## What the papers add

[ViMD, EMNLP 2024](https://aclanthology.org/2024.emnlp-main.426/) explicitly covers 63 provincial dialects. The repository has only three exposed ViMD test clips; those cannot rank regional robustness. Use balanced unseen speakers/provinces and report Northern/Central/Southern strata plus province when sample counts allow. [Original dataset card](https://huggingface.co/datasets/nguyendv02/ViMD_Dataset) carries CC-BY-NC-ND-4.0: it is a research diagnostic, not a blanket commercial training-data authorization.

[VLSP 2025 challenge analysis](https://aclanthology.org/2025.vlsp-1.1/) reports best ASR 9.07% syllable error rate, custom Zipformer and augmentation; it emphasizes data quality, Vietnamese tones and dialect variation. This is evidence to evaluate data/domain adaptation, not evidence that any released checkpoint will achieve that score in this shop. Preserve syllable error rate as an auxiliary Vietnamese metric and label the repository's whitespace WER normalization precisely. Competition scores and kiosk scores are not directly comparable.

The existing denoising ablation already found that RNNoise/GTCRN/DeepFilterNet could worsen Vietnamese WER. Therefore repeat paired clean/noisy experiments with each ASR model rather than selecting a denoiser from perceptual cleanliness. Include real HVAC/fan and playback echo; synthetic additive noise does not represent browser AEC or competing speakers.

## Uncertainty without pretending provider internals exist

[Huo et al., Identifying and Calibrating Overconfidence in Noisy Speech Recognition, 2025](https://arxiv.org/abs/2509.07195) studies Whisper-medium.en under synthetic additive noise, finding confident wrong tokens and proposing selective post-hoc calibration. Its token logits, entropy, margin and representations require local model access; it is neither a Vietnamese validation nor a Groq feature. Calibration also does not repair the recognized words by itself.

For local PhoWhisper/PhoASR, investigate token/logit calibration only after collecting Vietnamese ground truth. Evaluate ECE/Brier score alongside selective WER and risk versus coverage; calibrate on validation speakers and score unseen speakers. For Groq, train an utterance-level accept/retry policy using only returned avg_logprob/no_speech_prob/compression ratio, duration, clipping and speech-boundary evidence. Do not label exp(avg_logprob) as probability that the sentence is correct. Independent recognizer disagreement can trigger confirmation, but agreement is not proof because models share training ancestry/data. Measure wrong-accept rate, excessive retry rate and latency.

## Contextual biasing with catalog boundaries

[Sun et al., Interspeech 2023](https://www.isca-archive.org/interspeech_2023/sun23e_interspeech.html) adapts a tree-constrained pointer generator to Whisper while keeping Whisper weights frozen. This requires a trained bias module and decoder access; it is not equivalent to adding product names to a hosted prompt. [2025 follow-up](https://arxiv.org/abs/2509.09197) trains keyword-aware biasing on synthetic data, reinforcing the need to test targeted words and general words separately.

Groq documents a 224-token contextual prompt limit. A deployment-feasible experiment is a concise active-category vocabulary versus no prompt on the same audio, not a full catalog or instructions to invent missing brands. Preserve raw transcript, bias list and corrected transcript separately. Evaluate product-entity recall AND false product insertions, including ordinary Vietnamese near-homophones, negation, allergy/budget constraints and out-of-domain speech. Do not repeat the previous accent-stripping/cross-sentence catalog bug or use an LLM to silently guess a different utterance.

## Paired evaluation and rollout gate

1. Freeze manifests with SHA-256 WAV, source/license, duration, speaker/region, language, noise/SNR and reference. Retain current known examples as development regressions. Collect a separate held-out kiosk set (suggested initial pilot: at least 20 speakers across three regions, several sessions/devices, short retail requests and ordinary conversation). Human-adjudicate references before decoding; do not select by model success.
2. Decode identical 16-kHz mono WAVs through pinned Groq large/turbo, PhoWhisper small/large, PhoASR-small and Parakeet revisions. Freeze language/prompt/VAD/postprocessing, then ablate one factor at a time. Record failures and rejected/long inputs in denominators. Keep provider fallback traces separate from single-model scores.
3. Score raw aggregate WER/CER and syllable error; also entity precision/recall, brand false substitution, critical constraint/negation accuracy and silence hallucination. Report paired differences with speaker-cluster bootstrap intervals and region/noise slices. Score catalog postprocessing as a separate delta, and router accuracy conditional on a correct transcript.
4. Measure end-to-end p50/p95 latency, real-time factor, peak RAM/VRAM, cold starts, cost, fallback/retry rate and concurrent load on intended hardware. Parameter count is not a hardware sizing result. Quantized conversions require their own parity/quality row.
5. Predeclare acceptance criteria before viewing held-out results: meaningful Vietnamese improvement with no material clean-EN or critical-entity regression, p95 within kiosk budget, and terms cleared. Set numerical tolerances with the product owner; do not invent a universal safe WER threshold. Shadow candidates first, preserve the current decoder until results satisfy the gate.
6. Reproduce the screenshot by injecting its correct transcript into the same consultation session. Verify Apple versus fruit/product routing and follow-up intent independently of microphone and decoder. Track this as routing regression evidence, never ASR improvement.

This is research guidance only. Remaining evidence gaps are the user's exact failed microphone audio, real room echo/noise and a blind regional retail test; none is closed by a paper leaderboard.

