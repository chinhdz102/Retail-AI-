# Retail-AI Production Quality Master Report

**Assessment date:** 2026-10-28
**Release decision:** `NOT READY` for production; suitable for controlled demo only
**Working branch:** `codex/retail-text-quality-v4-20261019` (kept as-is; no new branch created)
**Benchmark base commit:** `1b1a9b55ba6d0e7d6e3f04c55f7e197c87c905e6`
**Catalog snapshot:** WinMart JSON, dated 2026-09-15; SHA-256 `a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`

This report separates deterministic regressions, synthetic evaluation, live provider probes, and human review. None of the synthetic scores below should be read as real-world consultation accuracy.

## 1. Executive summary

The current implementation improves exact product/category retrieval, multi-turn scope retention, alternative pagination state, runtime hot paths, and explicit soft-preference retention. It keeps catalog facts as the source of truth and does not convert unverified attributes into hard filters or claims.

The frozen development benchmark reports 100% on its generated exact-product, category, and hard-constraint checks. These scores are provisional because queries are templated from the same catalog, labels have not been independently reviewed, and they do not grade semantic suitability or naturalness. A live, manually written Groq probe returned HTTP 200 on all five turns. In the post-fix run, Qwen included “dễ chia” in its tool arguments and the response acknowledged it while leaving the plan items unchanged. Because the model itself emitted the preference on this repeat, the live run does not prove that the semantic guard rescued an omitted field; the focused unit test covers that path. This is one successful probe, not a statistical quality estimate.

**Decision:** do not release as a production retail advisor. Keep demo-only until there is a human-authored, blind held-out set, independent qrels review, multi-user API load testing, and a documented catalog knowledge/provenance path for product attributes. Current latency at concurrency 5–20 is also too high for a production conversational target.

## 2. Commit & environment

- Work stays on `codex/retail-text-quality-v4-20261019`; no branch was created for this continuation.
- Benchmark artifacts were generated from base commit `1b1a9b55ba6d0e7d6e3f04c55f7e197c87c905e6` with a dirty working tree. Artifacts explicitly record this state; they are not artifacts from a clean release commit.
- Local environment: Windows, Python 3.13+, `uv`; runtime catalog is the WinMart JSON snapshot. The imported dataset has 3,099 raw rows, 3,081 accepted products, and 18 quarantined rows.
- Provider configuration was present for the live probe. No API key or credential value is written to reports or artifacts. Voice was not evaluated in this task.
- The snapshot date is 2026-09-15. Price and stock must be presented as snapshot evidence, not guaranteed live store values.

## 3. Root cause

| Failure class | Evidence | Root cause / treatment |
| --- | --- | --- |
| Alternative recommendations repeated items | Audit of 50 synthetic conversations found 48/50 alternative turns overlapping prior IDs; 42/50 repeated the exact same five IDs. | Parser intent was counted as success without checking novelty. Session now tracks seen product IDs by scope, and exhaustion is explicit. |
| “Below” price boundary labels | H-037/H-082 expected equality under “dưới”. | Dataset generator used a strict phrase with an inclusive boundary. Dev cases now say “tối đa”; historic artifacts remain unchanged. |
| LLM omitted a newly stated preference | Live picnic follow-up “ưu tiên món dễ chia…” returned an empty `preferences` field. | Tool-call semantic guard now uses the shared parser to recover only explicitly cued, parser-supported soft preferences. It does not make them hard constraints. |
| Unsupported snack attribute | Snapshot had no verified spice field; snack need had zero candidates. | Correct data insufficiency, not a retrieval bug. System abstained from saying a snack was non-spicy and excluded it from totals. |
| High concurrency latency | In-process catalog endpoint p50 rose from about 0.71 s at 1 request to 3.86 s at 5 and 4.87 s at 20. | CPU-bound normalization, fuzzy scoring, and taxonomy work remain a bottleneck. Bounded caches helped, but current profile is not a capacity test or production pass. |
| Recommendation quality is under-measured | Generated exact-SKU tests select a catalog-derived gold item. | Exact name resolution is being measured, not whether a product is suitable for an indirect human need. Human qrels are still required. |

## 4. Research literature

| Work | Relevance and limitation | Decision for Retail-AI |
| --- | --- | --- |
| [Which Works Best for Vietnamese?](https://aclanthology.org/2026.findings-eacl.110/) | Compares retrieval approaches across Vietnamese domains; retrieval performance depends on task/domain. | Keep BM25/exact matching as measured baselines; do not generalize title-query scores to semantic needs. |
| [ProductAgent](https://aclanthology.org/2025.emnlp-industry.25/) | Conversational product search with clarification and structured interaction. Its benchmark/simulation setup is not a substitute for this store’s user evaluation. | Retain stateful needs and clarification; do not claim paper results transfer to this catalog. |
| [Hint-Augmented Re-ranking](https://aclanthology.org/2025.ijcnlp-short.19/) | Decomposes product-search hints and reranks candidates; requires relevance supervision. | Do not train/promote a reranker before product-level relevance labels exist. |
| [When in Doubt, Ask First](https://aclanthology.org/2025.findings-ijcnlp.27/) | Retrieval agent for ambiguous/unanswerable questions. | Preserve abstention and clarification when evidence is missing; calibration remains unmeasured. |
| [PEPPER](https://aclanthology.org/2025.findings-emnlp.1067/) | Target-free user simulation addresses target leakage in conversational recommender evaluation. | Treat generated conversations as development probes; obtain human-authored blind cases. |
| [Retrieval Augmented Spelling Correction for E-Commerce](https://aclanthology.org/2024.emnlp-industry.7/) | Uses catalog candidates when correcting noisy product queries. | Keep fuzzy correction bounded by actual catalog candidates; protect brand/variant identity. |
| [M3-Embedding / BGE-M3](https://arxiv.org/abs/2402.03216) | Multilingual dense, sparse, and multi-vector retrieval. The paper does not establish benefit on this WinMart task. | Keep as a candidate for a qrels-backed experiment, not production default. |
| [Qwen3 Embedding and Reranking](https://arxiv.org/abs/2506.05176) | Multilingual embedding/reranking model family with several model sizes. | Benchmark only after CPU, RAM, cold-start, and human relevance gates are defined. |
| [Amazon ESCI](https://www.amazon.science/code-and-datasets/shopping-queries-dataset-a-large-scale-esci-benchmark-for-improving-product-search) | Exact, substitute, complement, irrelevant grades are useful for product relevance labeling; market/catalog differ. | Reuse the graded-label idea, not its English/Amazon labels as Vietnamese gold. |
| [VN-MTEB](https://leaderboard.mteb.org/benchmark/VN-MTEB%20(vie,%20v1)) | Vietnamese embedding benchmark across multiple task types, not retail-specific relevance. | Use only to shortlist models, never as Retail-AI accuracy. |

Paper findings are design inputs, not evidence that any untested technique works here. For example, Vietnamese embedding scores do not substitute for product qrels, and a simulated dialogue benchmark does not certify live customer usefulness.

## 5. GitHub implementation references

- [ViRE](https://github.com/longstnguyen/ViRE) provides a reproducible Vietnamese IR evaluation layout (retrieval, fusion, qrels, metrics, and error inspection). The local evaluation work follows the separation of dataset, retrieval and grader, but does not claim a full reproduction.
- [Amazon ESCI data repository](https://github.com/amazon-science/esci-data) documents graded relevance labels. Retail-AI still needs its own Vietnamese annotator instructions and reviewed labels.
- [URASys](https://github.com/ura-hcmut/URASys) is a local retrieval-agent reference for clarification and abstention. Its infrastructure is not copied wholesale because it would add complexity before a local data-backed need is shown.
- No GitHub reference repository is treated as an authoritative source for this catalog’s price, stock, product properties, or shelf assignments.

## 6. Architecture decision

Keep a hybrid, evidence-bounded architecture:

1. Normalize language and parse explicit product identity, category, price, budget, quantity, negation, ranking cue, reference, and ambiguity.
2. Resolve taxonomy and retrieve candidates from the active repository.
3. Apply hard constraints only when a matching catalog field can verify them.
4. Rank with supported exact/lexical/catalog evidence and session state.
5. Use the agent model for conversational tool selection and explanation, with a semantic guard correcting bounded state omissions.
6. Abstain or ask a focused follow-up if a required attribute is unknown; never invent a SKU, price, stock fact, sales rank, or use claim.

Do not promote LLM structured extraction to the every-request fast path: its recorded ablation passed 20/60 cases, with 34 provider errors and 6 invalid/incomplete extraction outputs. Do not promote dense retrieval: it did not improve the measured catalog-title queries and has not been measured against independently labeled semantic needs.

## 7. Code changed

- `backend/api/main.py`: preserve cumulative alternative IDs/scope in session, clean pagination language, clarify exhaustion, and keep local smalltalk fallback bounded to greetings/farewells/wellbeing.
- `backend/services/sessions.py`: backward-compatible per-scope seen-product state.
- `backend/services/retail_tools.py`: bounded alternative exclusions and explicit exhaustion behavior.
- `backend/services/retail_agent.py`: preserve explicit soft preferences omitted by a model tool call; keep budget, party size, rejected items, and hard constraints intact.
- `backend/services/shopping_mission.py`: acknowledge a newly recorded preference without claiming the catalog proves product fit.
- `backend/services/smalltalk.py`: broaden wellbeing detection for the local response path.
- `backend/products/category_taxonomy.py`, `snapshot_repository.py`, `postgres_repository.py`: bounded caches for repeated taxonomy, token, and normalization work.
- `evaluation/semantic_intelligence_v2_benchmark.py`, `build_semantic_intelligence_v2_frozen.py`: fix alternative metric semantics, inclusive budget language, and deterministic frozen-dataset generation.
- `evaluation/api_runtime_load_sanity.py`: add in-process deterministic catalog load/heap probe, separating heap instrumentation from latency measurement.
- Added qrels schema/annotation guide/export workflow and regression coverage for state, alternative turns, injection protections, and the newly recovered soft preference.

The preference guard requires an explicit preference cue and an existing parser-supported soft preference. It does not infer arbitrary product suitability from the utterance, alter need requiredness, or assert that candidates satisfy the preference. The post-fix live run returned the expected acknowledgement, but did not change the drink-only plan because the required snack attribute remains unverified. The raw trace shows Qwen emitted the preference itself, so this live run is not evidence that the backend guard rescued a missing tool field.

## 8. Dataset provenance

Primary catalog: `data/winmart_products.json`, 3,081 accepted products from 3,099 rows; catalog SHA-256 `a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`.

The semantic-intelligence V2 frozen development set contains 300 generated query cases (100 exact product, 100 category, 100 hard-constraint) and 50 generated conversations / 200 turns. It is generated from catalog titles, taxonomy, and fields. Dataset SHA-256: `8f129beecee14b7c8ff1bbba106fae154a3321a55ef2efaad718e3493de27d3b`.

The live Groq probe has two manually written cases and five turns (picnic and Pantene). The author was also the evaluator; it is a diagnostic example set, not independent human evaluation. The earlier Vietnamese 200-query set remains development/regression because its cases were reviewed while fixing code. No true held-out human set is available in this run.

An additional manually authored parser/retrieval development set has 200 queries across 20 catalog leaf categories. Its category labels were selected before running this parser version, but the set was then inspected during development and is not blind. The recorded field accuracy was product type 98%, intent 95.31%, negative constraints 76.92%, and comparison exact match 80%; catalog retrieval was relevant@1 91% and relevant@5 93%. These results help locate negation/comparison gaps, not estimate blind quality. Artifact: [`independent_consultation_v2_dev_parallel_20261028.json`](artifacts/independent_consultation_v2_dev_parallel_20261028.json).

## 9. Gold-label audit

- Product identity, category, price and brand in generated cases are derived from snapshot rows. This supports deterministic identity checks, not semantic suitability.
- Generated category precision checks category membership, not fit to an intended use.
- Hard-constraint checks cover encoded fields and empty-result behavior. They cannot validate absent attributes such as “ngon”, “không cay”, “dễ chia”, efficacy, or suitability.
- The former alternative measure counted parser intent only. Its labels allowed 48/50 repeated prior SKUs and 42/50 exact repeated lists. It is now labeled intent-only; candidate novelty is `NOT_EVALUATED_REQUIRES_STATEFUL_API_SESSION` in the parser benchmark.
- H-037/H-082 wording was corrected in the development generator; old JSON artifacts were retained for audit.
- Annotation schema and review tooling are scaffolding only. No annotator-completed qrels were available; labels requiring judgment must remain `needs_review`.

## 10. Baseline vs experiments

| Measurement | Earlier baseline | Latest generated development run | Interpretation |
| --- | ---: | ---: | --- |
| Exact product Recall@1 / @5, n=100 | 0.32 / 0.37 | 1.00 / 1.00 | Improvement on catalog-derived exact product queries only. |
| Category Precision@5, n=100 | 0.80 | 1.00 | Taxonomy membership; not semantic usefulness. |
| Hard-constraint cases passed, n=100 | 0.57 | 1.00 | Generated constraints from represented fields only. |
| Constraint violations | 1 | 0 | Does not prove correctness for missing/unrepresented attributes. |
| Candidate Recall@5 | 0.7628 | 1.00 | Provisional synthetic candidate coverage. |
| Category scope / context / price ordering | Not comparable | 150/150; 100/100; 50/50 | Parser/tool harness, not response prose. |
| Alternative intent / candidate novelty | Not measured | 50/50 intent; novelty 0 cases | Intent recognition is not successful alternative recommendation. |
| Manually authored parser/retrieval dev set, n=200 | Direct-search relevant@1/.@5 .075/.090 | Tool relevant@1/.@5 .91/.93; product type 98%; intent 95.31%; negation 76.92%; comparison exact match 80% | Exposed development set, not blind; distinct from generated exact-match cases. |

Latest artifact: [`retail_ai_semantic_intelligence_v2_dev_rev2_20261028.json`](artifacts/retail_ai_semantic_intelligence_v2_dev_rev2_20261028.json). It uses the same synthetic dataset hash as rev1, so rev2 is not an independent holdout. Baseline and historical numbers are documented in [`retail_ai_semantic_intelligence_v2.md`](retail_ai_semantic_intelligence_v2.md).

## 11. Ablation by dimension

| Dimension | Result | Decision |
| --- | --- | --- |
| Deterministic parser + catalog | Dev product R@1/R@5 and category P@5 are 1.00 on generated cases. | Keep as low-latency/evidence-grounded path; do not infer human quality. |
| LLM structured extraction | 20/60 pass; 34 provider errors; 6 schema/parse/incomplete responses. | Keep model as conversational tool agent; do not call structured extraction for every query. |
| BM25 vs E5-small vs RRF, title-derived dev n=150 | BM25 R@1/R@5 .9800/1.0000; E5 .9667/.9800; RRF .9733/.9933. | BM25 remains competitive for explicit title search. |
| BM25 vs E5-small vs RRF, generated SKU-held-out title variants n=360 | BM25 .9889/1.0000; E5 .9389/.9833; RRF .9722/.9917. | Does not settle semantic-query retrieval; no model promoted. |
| Bounded in-memory caches | Deterministic ASGI p50 improved vs prior same-script run: approx 1,354→707 ms at C1, 7,541→3,855 ms at C5, 7,878→4,871 ms at C20. | Retain bounded caches, but remeasure on server deployment and real query traffic. |
| Soft-preference recovery | New unit tests prove explicit parser-supported preference survives an omitted LLM field and budget-only revision is not treated as preference. | Keep as a narrow generic state guard; post-fix live run passed but the model emitted the preference itself. |

BM25/dense comparison used title-derived paraphrase variants, not human semantic need queries. Cross-encoder and large embedding reranking were not implemented because qrels, hardware profile, and expected benefit are missing.

## 12. Error analysis

1. The previous alternative metric was misleading: 48/50 turns reused an item; 42/50 repeated all five IDs. Session novelty tracking is implemented, but the synthetic conversation harness still does not grade session-visible candidate novelty.
2. Picnic probe: the model proposed drinks, but the required “snack không cay” group had zero verified candidates because the catalog lacks a spice attribute. The answer disclosed this and did not count a substitute in the total. This is grounded abstention, though the plan is incomplete.
3. The first picnic run omitted the follow-up preference; the second run's raw Qwen trace included it. The second response said “Mình đã ghi nhận thêm ưu tiên bạn vừa nêu,” preserved the budget and unverified-snack status, and did not claim the product meets the preference. The selected items remained unchanged because the catalog has no shareability evidence. Unit tests separately verify the parser guard when a model call omits the field.
4. “Best seller” cannot be inferred from catalog order or snapshot stock. The probe abstained because verified sales data is absent.
5. Shelf position is explicitly labeled a recommended planogram (`A03-B03-L03-P07`), while the source row records `C1-L2`; it is not represented as verified physical placement.
6. Human naturalness, dialect robustness, ASR transcript tolerance, useful substitutions, and multi-intent accuracy remain unmeasured independently.

## 13. End-to-end conversation examples

**Picnic:** “Picnic cho 4 người, khoảng 200 nghìn, snack không cay và nước uống.” The response grounds Aquafina 5L at 26,900đ from the 15/09/2026 snapshot, describes stock as not real-time, says the snack attribute is unavailable, and does not add to the cart. Good grounding; incomplete shopping coverage is expected from missing evidence.

**Follow-up:** “Ưu tiên món dễ chia cho mọi người…” The first live run repeated the same drink-only plan and failed to acknowledge the new preference. On the repeat, Qwen itself included the preference in tool arguments and the response acknowledged it while keeping the 26,900đ plan unchanged. Unit tests verify the backend also preserves a parser-supported preference if a model omits it, while keeping budget/party size and hard requirements intact. The repeat therefore verifies end-to-end acknowledgement, but not the omitted-field guard under live model output. The catalog has no verified shareability field, so the system does not claim the selected drink is shareable.

**Pantene:** Exact query returned snapshot price 169,900đ; shelf follow-up returned `A03-B03-L03-P07` explicitly as a proposed location, with physical placement unverified; bestseller follow-up abstained because sales data is missing.

See [`live_snapshot_provider_probe_after_preference_fix_20261028.json`](artifacts/live_snapshot_provider_probe_after_preference_fix_20261028.json) for post-fix per-turn inputs, outputs, tools, IDs, and safety checks. [`generalized_live_hard_cases_20261028.json`](artifacts/generalized_live_hard_cases_20261028.json) records the separate `/agent/respond` and SSE smoke. Do not treat HTTP 200s as high-quality human-graded answers.

## 14. Quality gates

| Gate | Result | Status |
| --- | --- | --- |
| Full backend/evaluation pytest suite | `uv run pytest tests -q --tb=short`: 1,222 passed in 241.30 s. | Pass. |
| Ruff and `git diff --check` | `uv run ruff check backend tests evaluation`: all checks passed; `git diff --check` clean (Git emitted only line-ending conversion warnings). | Pass. |
| Synthetic product/category/constraint checks | 100/100 within each generated dimension; zero encoded-constraint violations. | Pass for development regression only. |
| Live API grounding invariants | Post-fix run: 5/5 turns HTTP 200; 5/5 automated grounding checks passed; no fallback used. | HTTP/grounding gate only; not human-graded. |
| Prompt-injection and unauthorized-write regressions | Covered by API/support tests for false price changes, secret requests, fake role/tool, payment, and ticket boundaries. | Regression coverage; not a penetration test or formal security certification. |
| Human qrels, naturalness, helpfulness, abstention calibration | Not completed. | Fail release gate. |
| Concurrency capacity / deployment SLA | Not measured; local in-process smoke degrades at C5/C20. | Fail production release gate. |
| STT/TTS voice quality | Not measured in this text-only evaluation. | No voice-quality claim. |

## 15. Latency/RAM/CPU

Latest [`api_runtime_load_sanity_20261028.json`](artifacts/api_runtime_load_sanity_20261028.json), deterministic catalog-only in-process ASGI; five requests submitted per concurrency level:

| Concurrency | p50 | p95 | mean | throughput | HTTP errors / ID-price mismatches |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 707.300 ms | 852.292 ms | 668.219 ms | 1.483 req/s | 0 / 0 |
| 5 | 3,855.158 ms | 3,873.236 ms | 3,858.476 ms | 1.289 req/s | 0 / 0 |
| 20 | 4,870.936 ms | 4,947.815 ms | 4,880.549 ms | 1.009 req/s | 0 / 0 |

A separate single-request `tracemalloc` probe measured Python heap current 395,778 B and peak 818,295 B. It excludes native allocations and process RSS. CPU in the timed smoke was 3,328 ms (C1), 3,750 ms (C5), and 4,609 ms (C20). The harness excludes network, browser, TLS, reverse proxy, worker pool, provider calls, STT, and TTS. Five requests per level are insufficient for capacity or SLA claims.

The caches lowered median latency relative to the same earlier smoke by about 48% at C1, 49% at C5, and 38% at C20, while absolute C5/C20 latency remains unsuitable for production chat.

## 16. Provider reliability

The post-fix live probe used Groq `qwen/qwen3.8-27b` for two turns and local deterministic/catalog paths for the other three. All five returned HTTP 200, all five passed automated grounding checks, and both model calls succeeded with the primary credential; no fallback was recorded. The two model turns consumed 7,599 reported tokens (3,344 + 4,255). This is a tiny single-run sample and does not estimate outage/fallback reliability or production latency distributions.

Per-turn API latency in the post-fix probe: 2,830 ms and 2,303 ms for the model-backed picnic turns, 293 ms, 222 ms, and 111 ms for catalog/local turns. The provider-call timings were 2,443 ms and 1,968 ms. These are one-run timings, not provider p50/p95. Credentials were configured, but secret values were excluded.

## 17. Security regression

Automated regression coverage checks that user-provided injection does not override catalog price, expose secrets, fabricate an admin role/tool, mutate payment/cart state without authorization, or create an unrelated support ticket. Relevant tests include `tests/test_retail_api.py` injection-context cases and `tests/test_support.py` ticket-policy cases.

The checks run in the full suite; they do not establish resistance to every prompt-injection technique, indirect document attack, model/provider compromise, or cross-tenant deployment issue. Tool authorization, session isolation, backend validation, and data provenance remain the primary security boundaries.

## 18. Human review status

**No independent human qrels or conversation-quality review was completed.** The 300 query / 50 conversation semantic benchmark is synthetic and development-facing. The two-case live probe was manually authored by the implementer and inspected by the implementer, so it is diagnostic only.

Annotation schema, guidance, and exporter are present under `evaluation/schemas/`, `evaluation/review/`, and `evaluation/vietnamese_product_qrels_annotation_guide.md`. The next review should use at least two annotators for ambiguous Exact/Substitute/Complement/Irrelevant labels, adjudicate disagreements, and freeze a separately authored blind set before tuning against it.

## 19. Remaining limitations

- Catalog freshness is 2026-09-15; stock and price may have changed.
- Descriptions are sparse/repetitive; many efficacy, flavor, spice, audience, compatibility, material, and use attributes are unavailable.
- No verified sales, rating, or popularity data; bestseller/tastiest claims must abstain.
- Shelf planogram is synthetic/recommended and not proof of physical placement.
- Search metrics do not validate suitability for implicit needs, accepted substitutes, complements, or multi-product tasks.
- Synthetic development cases have leakage risk and narrow language coverage; accentless text, typos, dialects, ASR errors, and long multi-intent conversations need independent review.
- Alternative SKU novelty is not measured by the parser benchmark; current load test is too small for capacity estimates.
- No actual browser/network E2E or voice/STT/TTS benchmark is part of this report.
- Post-fix preference handling now has focused unit coverage and one successful live provider re-run; repeated sessions and broader preference types still need independent coverage.

## 20. Release decision

**NOT READY for production. Demo-only with an operator aware of snapshot staleness and unknown product attributes.**

The implementation passes deterministic development regressions and guarded grounding examples, but lacks evidence for the core release claim: accurate and natural advice on independently written, unseen Vietnamese requests across the whole catalog. Production should be reconsidered after human-reviewed blind evaluation, acceptable API load profile, reliable current store data, and successful live preference/session revalidation.

## 21. Reproduction commands

Run from repository root:

```powershell
uv run pytest tests -q --tb=short
uv run ruff check backend tests evaluation
uv run pytest tests/test_retail_agent.py tests/test_shopping_mission.py -q --tb=short
uv run python -m evaluation.semantic_intelligence_v2_benchmark `
  --dataset evaluation/cases/retail_ai_semantic_intelligence_v2_dev_rev1_20261028.json `
  --output docs/reports/artifacts/retail_ai_semantic_intelligence_v2_dev_rev2_20261028.json
uv run python -m evaluation.api_runtime_load_sanity `
  --output docs/reports/artifacts/api_runtime_load_sanity_20261028.json
uv run python -m evaluation.live_text_conversation_benchmark `
  --case-file evaluation/cases/vietnamese_snapshot_provider_probe_20261028.json `
  --pace-seconds 2.5 `
  --output docs/reports/artifacts/live_snapshot_provider_probe_after_preference_fix_20261028.json
git diff --check
```

Live Groq execution requires credentials in local environment/`.env`; do not print or commit them. The live command is text-only, can consume provider quota, and is not an independent statistical evaluation.

## 22. Rollback plan

1. Keep this work on the existing branch until review; do not merge to `main` as part of this task.
2. If a regression is found, revert the specific code commit on this branch (or use `git revert <commit>` after it is pushed); do not reset/force-push shared history.
3. The bounded cache changes can be reverted independently from session alternative tracking and preference preservation. Preserve regression tests and benchmark artifacts when reverting production behavior so the defect remains visible.
4. If the preference parser causes noisy ranking, disable the soft-preference recovery path first; hard requirements and budget validation must stay enabled.
5. Restore the prior report/artifacts from Git history if a report correction is needed; retain historical benchmark JSON and never overwrite it with a post-fix run.
