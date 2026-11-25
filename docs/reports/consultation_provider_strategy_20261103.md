# Provider strategy research — 3 November 2026

No model requests or production edits were made for this memo. Public documentation and
paper abstracts were fetched; these are method-level recommendations, not reproduced paper
results. Source text is cached in `tmp/groq_research`.

## What the primary sources establish

- [Groq structured outputs](https://console.groq.com/docs/structured-outputs), read today,
  explicitly lists `qwen/qwen3.8-27b`, `openai/gpt-oss-120b`, and `openai/gpt-oss-20b`
  for `strict:true`. All object properties must be required, and objects must forbid
  additional properties. JSON Object Mode guarantees JSON syntax rather than our schema;
  best-effort `strict:false` can still fail validation. Streaming/tool use is not supported
  with structured outputs. Thus Qwen is supported; a captured 400 is not proof otherwise.
- The same page promises exact strict adherence, but the recorded 120B generation omitted
  `required_size` and raised `json_validate_failed`. Keep this observed provider-contract
  failure distinct from a capability claim. Local default recovery addresses this narrow
  mismatch; arbitrary failed generations still need strict validation and business review.
- [Groq rate limits](https://console.groq.com/docs/rate-limits) lists free-plan limits of
  8K TPM, 200K TPD, 30 RPM and 1K RPD for these three models. Runtime response headers remain
  authoritative for this account. Published developer-plan limits in the
  [models table](https://console.groq.com/docs/models) must not be substituted for runtime
  free-plan capacity. Reset-request headers represent RPD and reset-token headers TPM;
  other limits can bind first, with Retry-After giving the retry wait.
- [JSONSchemaBench](https://arxiv.org/abs/2501.10868) evaluates efficiency, constraint
  coverage and output quality separately across 10K real schemas. Its method supports
  measuring our own schemas, tokens and semantic validity separately; it does not establish
  Vietnamese retail accuracy or rank these Groq models for this application.
- [tau-bench](https://arxiv.org/abs/2406.12045) evaluates dynamic policy/tool conversations
  by final database state and repeated-trial reliability (pass^k). Apply the principle to
  purpose, clarified slots, grounded selections and cart state across whole journeys;
  a fluent single answer or one successful run is insufficient. Its retail domain is not
  this kiosk's Vietnamese advisory task; do not transfer its success percentages here.
- [ALCE](https://aclanthology.org/2023.emnlp-main.398/) separates fluency, correctness and
  evidence/citation quality. A valid SKU and natural prose do not establish the claimed
  benefit, preparation method or customer fit. Preserve claim-level evidence checks.
- [Self-Refine](https://arxiv.org/abs/2303.17651) uses feedback/refinement across seven
  tasks. It motivates bounded critique/repair, not a guarantee of retail truth. The
  application's observed extra stages consume quota and sometimes change the task;
  measure useful repairs per token and avoid treating self-approval as verification.

## Offline diagnosis

`live_advice_direct_trace_20261103.json` records five successful stages: input tokens
3,383 / 4,371 / 1,957 / 4,976 / 2,293 = 16,980; completions add 3,227 for 20,207 total.
This excludes failed generations without usage, so it is a lower bound on consumed work.
Multiple Qwen keys and then 120B were needed; this is not a clean model comparison.
The two selector contexts each contain 7,900 UTF-8 bytes of candidate data. The planner
contains 7,193 bytes of category groups. Safe derived-placeholder compaction saves 3,172
message bytes (5.26% overall), as recorded in `consultation_context_compaction_20261103.json`;
new provider token counts remain unmeasured.

`live_hotpot_direct_trace_v2_20261103.json` shows quota errors, Qwen invalid JSON, then
120B valid JSON missing one local-default field, followed by a 20B success. The 120B
failure also has six schema-approved unused null strings. The guarded comparator now
accepts only this kind of declared-default/wire-null normalization. This prevents a
needless fallback but does not approve the proposed advice or endorse 120B as superior.

## Feasible staged experiment within the current service

1. Freeze input audio/text, retrieved candidates, source facts, slot memory, policies,
   schema hashes and completion budgets. Evaluate planner, selector and reviewer
   independently before measuring complete journeys. Do not compare fallback mixtures
   as if only the model changed. Record model/key slot, 400/429/cooldown, local recovery,
   valid output, local rejection, input/output tokens, latency and useful answer.
2. Compare Qwen strict and 120B strict on the same frozen stage inputs. Keep 20B as an
   explicitly measured baseline; do not silently equate a fallback success with equal
   advice quality. No evidence currently justifies switching every stage's primary.
3. Add a diagnostic JSON-object arm for each model: include the exact local schema in
   the prompt, require JSON, and run the same strict local schema, default comparator,
   grounded renderer and independent reviewer. This may avoid provider-side schema 400s
   but increases schema text/token cost and can increase invalid outputs. Never publish
   a raw JSON-mode answer or loosen types/extra fields to improve pass counts.
4. Stage-specific model routing is feasible after this comparison: reserve the more
   reliable model for the measured stage, with its own explicit fallbacks. Test Qwen
   reasoning `none` versus `low` separately using identical stage inputs; shorter
   completion alone is not evidence of quality. Retain capacity-aware waits rather
   than consuming more keys/models immediately after each known quota failure.
5. Measure held-out multi-turn tasks with paraphrases and repeats: useful preliminary
   advice, one essential question, no repeated answered slot, no invented suitability,
   correct alternatives/basket roles, exact quantities/prices, safe preparation, and
   coherent follow-up. Count unavailable/fallback responses as task failures rather
   than excluding them. Add human review of semantic quality to deterministic checks.

Prefer eliminating demonstrated redundant repair cycles over removing review. A three-stage
planner/selector/reviewer path already needs careful capacity scheduling at 8K TPM. No
prompt-only adjustment guarantees sustained kiosk throughput under that limit, and a
non-paid strategy must tolerate bounded latency or honestly report temporary unavailability.
