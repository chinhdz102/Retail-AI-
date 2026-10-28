# Retail-AI Production Quality Master Report

**Assessment date:** 2026-10-29
**Release decision:** `NOT READY` for production; suitable for controlled demo only
**Working branch:** `codex/retail-text-quality-v4-20261019` (kept as-is; no new branch created)
**Historical benchmark base commit:** `7eeaa179d0ca0753a9759e67df3e4b2bf494a0ae`
**Catalog snapshot:** WinMart JSON, dated 2026-09-15; Windows raw-byte SHA-256 `a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`; LF-normalized content SHA-256 `8f2ee3771be548b85a8d8981127b2518b8e8e5868584491df36b96317476cbbc`.

This report separates deterministic regressions, synthetic evaluation, live provider probes, and human review. None of the synthetic scores below should be read as real-world consultation accuracy.

## 1. Executive summary

The current implementation improves exact product/category retrieval, multi-turn scope retention, alternative pagination, runtime hot paths, and explicit soft-preference retention. This continuation fixes alternative lookup truncation beyond the repository's 20-row page, ties session novelty to products actually shown, and adds per-attribute catalog evidence (`VERIFIED` / `INFERRED` / `UNKNOWN`) with provenance. Simulated and demo shelf coordinates are no longer described as physically verified. The retrieval comparison now uses the exact 3,081-product corpus accepted by the runtime repository for BM25, E5-small, and RRF; the old 3,099-row raw-corpus comparison is retained only as historical evidence. A blinded two-reviewer queue now pools all three systems on the same 150 development queries. The latest changes route natural comparisons through grounded candidate selection and list/availability questions naming two to five distinct taxonomy categories through parallel, category-filtered catalog searches. Product flavors/variants resolving to one category remain a single-category search. After a category listing, ordinal factual follow-ups resolve against displayed product IDs, “cheapest” compares only displayed candidates, and an explicit “quay lại món ban đầu” reference resolves to the first product from the active browse flow. The API also preserves the visible candidate list through a detail-price question and supports removing a named, ordinal, or clearly referenced item from that recommendation list without changing the cart. Retrieval evaluation fixes multi-gold Recall@k and adds paired uncertainty estimates clustered by shared gold product IDs.

The frozen development benchmark reports 100% on generated exact-product/category checks and zero encoded hard-constraint violations, but its candidate Recall@5 is 0.6878 after correcting a grader bug (98 cases had eligible candidates; two were separately nonempty-result failures). A second generated dev set scores 0.6841 over 100 evaluable hard-constraint cases. The old 0.98–1.00 values used an invalid denominator/rank window and are superseded. All results remain provisional because queries are templated from the same catalog, labels have not been independently reviewed, and they do not grade semantic suitability or naturalness. A live, manually written Groq probe returned HTTP 200 on all five turns. In the post-fix run, Qwen included “dễ chia” in its tool arguments and the response acknowledged it while leaving the plan items unchanged. Because the model itself emitted the preference on this repeat, the live run does not prove that the semantic guard rescued an omitted field; the focused unit test covers that path. This is one successful probe, not a statistical quality estimate. Latest focused flow/evaluation tests pass 379/379 and the complete backend suite passes 1,294 tests.

**Decision:** do not release as a production retail advisor. Keep demo-only until there is a human-authored, blind held-out set, independent qrels review, multi-user API load testing, and real attribute-level catalog evidence. The new evidence tool prevents missing attributes from being guessed; it does not add currently absent product facts. Current latency at concurrency 5–20 is also too high for a production conversational target.

## 2. Commit & environment

- Work stays on `codex/retail-text-quality-v4-20261019`; no branch was created for this continuation.
- The 2026-10-29 semantic V2 rerun used base commit `7eeaa179d0ca0753a9759e67df3e4b2bf494a0ae` with a dirty working tree; its artifact records the tracked diff hash. It is not a clean release-commit result. Historical probes listed below may use earlier base commits.
- Full-suite code under test: commit `68da9468dee3764ce373ea092de501506b270ee0` on existing branch `codex/retail-text-quality-v4-20261019`; 1,267 tests pass locally in 171.53 s and GitHub Actions run [`40759292984`](https://github.com/chinhdz102/Retail-AI-/actions/runs/40759292984) passes lint, the offline backend suite, and compile on Ubuntu. No branch was created.
- Latest same-branch continuation adds two end-to-end follow-up regressions: cheapest-item ranking must use only IDs in the preceding displayed list, and an ordinal price question must resolve to the product at that displayed position. The initial ordinal test exposed a real failure; after the fix, the full local suite passes 1,269 tests in 170.65 s. Ruff, compileall, and `git diff --check` also pass. GitHub Actions run [`40761103420`](https://github.com/chinhdz102/Retail-AI-/actions/runs/40761103420) passes lint, the offline backend suite, and compile on commit `29413e20fc9c2f50dd2a2dd294c35dd0f8abec8d`. No API calls or paid-provider benchmark were used.
- Current same-branch continuation generalizes explicit multi-category list/availability requests to two through five distinct taxonomy groups and fixes “quay lại món ban đầu” after alternative pages by retaining up to 10 prior pages plus a pinned opening page per browse scope. The exact original product ID and snapshot price are covered by an API regression; a session test confirms the opening page survives more than 10 pages and resets on a new category/scope. The final full local suite passes 1,280 tests in 209.32 s; the focused parser/session/catalog/API set passes 126 tests in 58.43 s. Ruff, compileall, and `git diff --check` pass. Code commit `350505e8adba722d6ea21766aa748fa59d987b79` passes GitHub Actions run [`40765305100`](https://github.com/chinhdz102/Retail-AI-/actions/runs/40765305100) (lint, offline suite, compile); no new branch was created.
- This continuation stays on the same branch and adds list-only removal after product-detail follow-ups, preserving the visible page across a narrowed detail lookup. Four categories (bao, face towels, bottled water, shampoo) cover ordinal price lookup and removal by pronoun/ordinal; ambiguous references ask which item and preserve both the remaining list and cart. The focused API/session/evaluator suite passes 379 tests; the full backend suite passes 1,294 tests in 174.64 s. Ruff and `git diff --check` pass. Benchmark artifacts record the pre-run base commit and dirty-tree fingerprint; the recorded runs were made against a dirty working tree before this continuation was committed.
- Baseline full suite on `7eeaa17`: 1,222 passed in 299.31 s. A previous continuation passed 1,233 tests in 217.86 s. The seeded-invariant run based on `af1f979b7c4ce01b1f7aef3f19523ababcb3a6b4` passed 1,238 in 248.48 s. After the qrels exporter and branch-CI changes, a full suite passed 1,238 tests in 250.70 s based on `b5d46c9`. The preceding pushed code passed 1,241 tests locally in 157.31 s and on GitHub Actions run `40748503030` for commit `42479e7` in 151.11 s. Commit `89472bf` passes 1,252 tests locally in 210.89 s; GitHub Actions run `40755659331` passes on Ubuntu for the same SHA.
- New deterministic regression suite: 5/5 tests passed in 14.66 s using seed `20261029` and the catalog hash below. It sampled 64 SKUs for title variants, checked 17 retrievable price-filter scopes, exercised 32 brand-exclusion scopes, tried 32 unseen query tokens, and rebuilt an index from one synthetic new catalog row. Artifact: [`catalog_metamorphic_invariants_20261029.json`](artifacts/catalog_metamorphic_invariants_20261029.json).
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
| Alternative search stopped after seen products filled page one | Repository caps results at 20; asking for 100 still returned 20, which could be mistaken for catalog exhaustion. | Added stable `offset` pagination for demo, JSON snapshot, and Postgres repositories. Alternative lookup now advances page-by-page and reports exhaustion only at the last page. |
| Session remembered filtered-out candidates | Candidate state was captured before audience/hard filtering while the UI showed a narrower product list. | Session selected/seen IDs now come from the final visible product cards, so the next turn tracks what the customer actually saw. |
| “Quay lại món ban đầu” lost its first result after pagination | Session state kept only the latest `selected_product_ids`, so a later page replaced the original list and an ordinal-like follow-up could resolve against the wrong products. | Keep up to 10 prior displayed pages per active browse scope; clear history on a fresh recommendation, scope change, or category change. Resolve explicit original-item references to the first product ID on the first page, then use the normal snapshot tool for the requested facts. |
| Demo shelf looked verified | `DEMO`, `SIMULATED`, and planogram sources are not proof of physical placement. | Shelf evidence is `VERIFIED` only when explicitly marked `PHYSICALLY_VERIFIED`; other stored coordinates are qualified as inferred. |
| Shelf lookup failed only on Linux CI | Run `40744779694` failed two natural shelf lookups and one inferred-shelf provenance assertion; all three passed locally. GitHub checked out the catalog as LF, while the planogram hash had been generated from Windows CRLF bytes. | Planogram metadata now carries both the legacy raw-byte hash and an LF-normalized content hash. Lookup accepts only one of those exact catalog fingerprints, preserving fail-closed behavior while making the same snapshot portable across operating systems. The Linux checkout simulation passes locally, and GitHub Actions run `40748503030` now passes the full suite.
| High concurrency latency | The earlier five-request smoke reported p50 0.71/3.86/4.87 s at configured C1/C5/C20. The corrected 20-request run measures 0.131/0.515/2.552 s, with all 20 requests simultaneously in flight at C20. | Caching reduced the measured path, but catalog work still serializes under CPU load; C20 remains too slow. The corrected in-process profile is not a deployment capacity or production pass. |
| Recommendation quality is under-measured | Generated exact-SKU tests select a catalog-derived gold item. | Exact name resolution is being measured, not whether a product is suitable for an indirect human need. Human qrels are still required. |
| List follow-up lost candidate context | Asking the price of the second displayed product narrowed the session to that SKU; the later “bỏ loại đó khỏi danh sách” could lose the original choices and return an empty list. | Preserve page history when a visible candidate is narrowed to its detail; remove only from the active displayed recommendation list, clarify ambiguous pronouns, and leave the cart unchanged. |
| Hard-constraint Recall@5 overstated coverage | The old grader filtered invalid rows before slicing top five and divided by `min(5, eligible_count)`, confusing top-k hit coverage with recall. | Use the raw top-five ranks divided by all eligible gold IDs; report empty-gold cases as non-evaluable and report the evaluable count separately. Old 0.98–1.00 values are deprecated. |

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
7. For a selected product's requested attributes, preserve value, evidence status, source, observation/verification date, catalog version, and confidence. Do not promote inferred retailer copy or simulated placement to verified evidence.

Do not promote LLM structured extraction to the every-request fast path: its recorded ablation passed 20/60 cases, with 34 provider errors and 6 invalid/incomplete extraction outputs. Do not promote dense retrieval: it did not improve the measured catalog-title queries and has not been measured against independently labeled semantic needs.

## 7. Code changed

- `backend/api/main.py`: preserve cumulative alternative IDs/scope in session, clean pagination language, clarify exhaustion, and keep local smalltalk fallback bounded to greetings/farewells/wellbeing.
- `backend/services/sessions.py`: backward-compatible per-scope seen-product state.
- `backend/services/retail_tools.py`: bounded alternative exclusions and explicit exhaustion behavior.
- `backend/products/knowledge.py` and `get_product_knowledge`: per-attribute evidence envelope with provenance and explicit unknowns; the agent can call it only for a uniquely grounded product candidate. This is a guard/schema, not a multi-category product-data enrichment: the current model has no source URL or reviewer identity and the snapshot itself has no verified ingredient/spice facts.
- `backend/products/repository.py`, `snapshot_repository.py`, and `postgres_repository.py`: stable bounded offset pagination used to walk beyond page one.
- `backend/api/main.py`: for an explicitly identified product, retrieve attribute evidence before abstaining on benefits, taste, usage, nutrition, allergen, and suitability questions; responses name the grounded product and state the missing evidence. Unknown product references still trigger clarification/abstention. Session state remembers only products actually rendered to the customer.
- `backend/services/retail_agent.py`: preserve explicit soft preferences omitted by a model tool call; keep budget, party size, rejected items, and hard constraints intact.
- `backend/services/shopping_mission.py`: acknowledge a newly recorded preference without claiming the catalog proves product fit.
- `backend/services/smalltalk.py`: broaden wellbeing detection for the local response path.
- `backend/products/category_taxonomy.py`, `snapshot_repository.py`, `postgres_repository.py`: bounded caches for repeated taxonomy, token, and normalization work.
- `backend/services/vietnamese_intent.py`: cache compiled boundary/cue patterns and pre-normalize static category cues; a 50-query parser microbenchmark retains 150/150 outputs and cuts repeated-path CPU time by 8.85× on this Windows run.
- `backend/data/catalog_import.py`, `scripts/generate_planogram.py`, `backend/products/planogram.py`, `data/winmart_planogram_assignments.json`: add a stable LF-normalized catalog fingerprint alongside the original raw hash so recommended shelf locations still resolve after Windows/Linux checkouts.
- `evaluation/semantic_intelligence_v2_benchmark.py`, `build_semantic_intelligence_v2_frozen.py`: fix alternative metric semantics, inclusive budget language, and deterministic frozen-dataset generation.
- `evaluation/export_vietnamese_qrels_review.py`: accept multiple same-query retrieval artifacts, form a rank-interleaved/deduplicated candidate union, hide retrieval sources and ranks, and independently randomize query/candidate order for reviewer A/B. An evaluator-only manifest records dataset/catalog/artifact hashes and candidate counts.
- `.github/workflows/retail-backend-quality.yml`: run locked-dependency Ruff, offline backend tests, and Python compile checks on `main` and the requested text-quality branch. The initial run failed on cross-platform shelf/hash assertions; run `40748503030` passes all three CI steps on commit `42479e7`.
- `evaluation/api_runtime_load_sanity.py`: exercise at least 20 requests at each C1/C5/C20 level, report observed in-flight concurrency and p99/max samples, capture environment and tracked-diff hash, and keep warm-up/heap probes separate from timed latency.
- `evaluation/vietnamese_parser_hot_path_benchmark.py`: compare current parser functions against pinned base `295133b`; artifact [`vietnamese_parser_hot_path_20261029.json`](artifacts/vietnamese_parser_hot_path_20261029.json) reports 150/150 output parity and 8.846× repeated-path speedup. This is a development microbenchmark, not a human semantic-quality score.
- `evaluation/generalized_retrieval_variants.py`: compare BM25, multilingual E5-small, and reciprocal-rank fusion on the runtime-validated catalog; validate gold product IDs before scoring and record catalog/query/commit hashes, full query latency, CPU, environment, and index-build time. The temporary `fastembed` environment is not added to project dependencies.
- `backend/services/product_comparison.py` and `backend/api/main.py`: let the shared structured query parser supply natural comparison operands when the local grammar does not match; for category-level ambiguity, search both sides and ask the shopper to choose exact products from grounded catalog candidates rather than compare arbitrary SKUs.
- `backend/services/catalog_browse.py`, `backend/products/category_taxonomy.py`, `backend/services/retail_agent.py`, and `backend/api/main.py`: for clear list/availability questions, resolve two to five distinct category mentions against the active taxonomy, search each through the bounded catalog-tool gateway, group only returned snapshot products, disclose unverified live stock, and retain grounded product IDs for follow-up turns. Same-category flavor/variant pairs, comparisons, price/location questions, and ambiguous categories do not enter this route. Added taxonomy aliases for common “cá biển” and “thịt bò” wording; no SKU-specific routing was added.
- `backend/api/main.py`: resolve factual ordinal follow-ups (for example, “giá sản phẩm đầu tiên?”) against the most recently displayed catalog candidate list, and keep the existing cheapest-price follow-up restricted to the saved displayed IDs.
- `backend/services/query_understanding.py`, `backend/services/sessions.py`, and `backend/api/main.py`: recognize explicit original-item references, store up to 10 prior visible product pages scoped to the active browse flow, and resolve the first item through the snapshot lookup path. Fresh recommendations and changed categories/scopes reset that history to prevent cross-query product confusion. Redis decoding remains compatible with session records written before this field existed.
- `backend/services/sessions.py` and `backend/api/main.py`: retain the candidate page when a user selects one displayed item for a factual detail follow-up; add a generic remove-from-current-list action by name, ordinal, or unambiguous deictic reference. Ambiguous references request clarification, and the operation only updates recommendation state, never cart contents. Tests cover bao, face towels, bottled water, shampoo, negated removal, ordinal removal, and unchanged cart state.
- `evaluation/generalized_retrieval_variants.py`: correct Recall@k to divide by the complete relevant-ID set and report paired product-cluster bootstrap confidence intervals; the resampling unit groups paraphrases connected by shared gold product IDs.
- `evaluation/semantic_intelligence_v2_benchmark.py`: correct hard-constraint candidate Recall@5 to use all eligible IDs as denominator and the actual unfiltered rank-1–5 window; cases without eligible products now have no recall value and are counted separately. New integration/unit tests prevent the previous inflated 0.98–1.00 scores from returning.
- `tests/test_product_comparison.py`, `tests/test_contextual_consultation_regressions.py`, `tests/test_catalog_browse.py`, and `tests/test_generalized_retrieval_variants.py`: cover natural category comparison and availability-query negative control, cross-department category browsing across beverages/fish/produce/meat, exact SKU/category grounding, retained session candidates, cheapest/ordinal follow-ups constrained to displayed products, and negative controls for same-category flavors/egg types and non-browse intents; also cover multi-gold recall, deterministic cluster bootstrap, and cluster linkage.
- `evaluation/export_vietnamese_qrels_review.py`: only export query cases present in a retrieval artifact, reject candidates outside the validated runtime catalog, preserve explicit no-candidate queries, and normalize content hashes across Windows/Linux line endings.
- `tests/test_generalized_retrieval_variants.py`: cover runtime-catalog parity, quarantined rows, held-in catalog IDs, and invalid gold IDs; benchmark/export regressions also cover missing-run cases.
- Added qrels schema/annotation guide/export workflow and regression coverage for state, alternative turns, injection protections, and the newly recovered soft preference.

The preference guard requires an explicit preference cue and an existing parser-supported soft preference. It does not infer arbitrary product suitability from the utterance, alter need requiredness, or assert that candidates satisfy the preference. The post-fix live run returned the expected acknowledgement, but did not change the drink-only plan because the required snack attribute remains unverified. The raw trace shows Qwen emitted the preference itself, so this live run is not evidence that the backend guard rescued a missing tool field.

## 8. Dataset provenance

Primary catalog: `data/winmart_products.json`, 3,081 accepted products from 3,099 rows; catalog SHA-256 `a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`.

The runtime-validated repository accepts 3,081 products from 3,099 rows and quarantines 18. Raw catalog SHA-256 is `a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`; LF-normalized content SHA-256 is `8f2ee3771be548b85a8d8981127b2518b8e8e5868584491df36b96317476cbbc`. New retrieval artifacts use only the accepted products, and every gold ID is validated against that corpus before retrieval.

The semantic-intelligence V2 benchmark includes frozen variants. The 2026-10-29 rerun used 300 generated query cases (100 exact product, 100 category, 100 hard-constraint) and 50 generated conversations / 200 turns from `retail_ai_semantic_intelligence_v2_frozen_20261028.json`, SHA-256 `d030d8f860f426a0ffcba7d44b542f8f7b23e9d361a51d8810c052670ba8b1e9`. The corrected Recall@5 is 0.6878 across 98 evaluable hard-constraint cases; two empty-gold cases are excluded from recall and separately reflected in nonempty-result rate 0.98. Earlier `dev_rev1` / `dev_rev2` artifacts use a separate file/hash (`8f129beecee14b7c8ff1bbba106fae154a3321a55ef2efaad718e3493de27d3b`); corrected rerun Recall@5 is 0.6841 across all 100 evaluable cases. These are synthetic, templated catalog-derived development data, not blind or human-reviewed. An audit found 280/300 exact query-case matches and all 50/50 conversations match between the purported frozen set and dev_rev1 despite distinct file hashes and the same generator seed; the two files are therefore not independent splits.

The live Groq probe has two manually written cases and five turns (picnic and Pantene). The author was also the evaluator; it is a diagnostic example set, not independent human evaluation. The earlier Vietnamese 200-query set remains development/regression because its cases were reviewed while fixing code. No true held-out human set is available in this run.

The source audit did not find a safe SKU-level enrichment pilot. The [Pantene Vietnam page](https://www.pantene.com.vn/vi-vn/mua-s%E1%BA%A3n-ph%E1%BA%A9m/d%E1%BA%A7u-g%E1%BB%99i-su%C3%B4n-mu%E1%BB%99t-%C3%B3ng-%E1%BA%A3) matches a product line but omits GTIN and package size, so it cannot verify the catalog record's barcode or `650g` value. [Nestlé Vietnam](https://www.nestle.com.vn/vi/brands/coffee/nescafe-cafe-ca-phe-hoa-tan) and its [official brochure](https://www.nestle.com.vn/sites/g/files/pydnoa216/files/2023-01/Nestle-IMS-Export-Brochure.pdf) do not resolve a `25×20g` product-title versus `25×24g` URL-slug conflict in one catalog record. The Vinamilk result was a staging page with no barcode; official Coca-Cola formula pages found were for other markets; Unilever's Sunlight page was brand-level. No manufacturer fact was imported or labeled verified.

An additional manually authored parser/retrieval development set has 200 queries across 20 catalog leaf categories. Its category labels were selected before running the parser version, but the set was then inspected during development and is not blind. The post-category-browse rerun remains at product type 98%, intent 95.31%, negative constraints 76.92%, and comparison exact match 80%; end-to-end case exact match is 95% (190/200). Tool retrieval is relevant@1 90.5%, relevant@5 92.5%, and category precision@5 89.7%. There are 10 exact-case failures, including comparison intent/type mismatches and three negative-constraint spans; these do not establish that an incorrect product was returned. Results are diagnostic and do not estimate blind quality. New artifact: [`independent_consultation_v2_post_category_browse_20261029.json`](artifacts/independent_consultation_v2_post_category_browse_20261029.json); prior run: [`independent_consultation_v2_dev_parallel_20261028.json`](artifacts/independent_consultation_v2_dev_parallel_20261028.json).

## 9. Gold-label audit

- Product identity, category, price and brand in generated cases are derived from snapshot rows. This supports deterministic identity checks, not semantic suitability.
- The two current V2 generated files are not independent query splits: a file-level audit found 280/300 identical `(case_id, query)` pairs and all 50/50 conversations/user turns match. Different JSON hashes do not make the data independent. The 120-SKU-disjoint generalized “heldout” set also uses title-derived templates and overlapping taxonomy templates, so it is not a blind semantic test.
- Generated category precision checks category membership, not fit to an intended use.
- Hard-constraint checks cover encoded fields and empty-result behavior. They cannot validate absent attributes such as “ngon”, “không cay”, “dễ chia”, efficacy, or suitability.
- The former alternative measure counted parser intent only. Its labels allowed 48/50 repeated prior SKUs and 42/50 exact repeated lists. It is now labeled intent-only; candidate novelty is `NOT_EVALUATED_REQUIRES_STATEFUL_API_SESSION` in the parser benchmark. The implementation has a deterministic page-walk regression, but this benchmark still does not grade end-to-end session novelty.
- H-037/H-082 wording was corrected in the development generator; old JSON artifacts were retained for audit.
- Annotation schema and review tooling are scaffolding only. No annotator-completed qrels were available; labels requiring judgment must remain `needs_review`.

## 10. Baseline vs experiments

| Measurement | Earlier baseline | Latest generated development run | Interpretation |
| --- | ---: | ---: | --- |
| Exact product Recall@1 / @5, n=100 | 0.32 / 0.37 | 1.00 / 1.00 | Improvement on catalog-derived exact product queries only. |
| Category Precision@5, n=100 | 0.80 | 1.00 | Taxonomy membership; not semantic usefulness. |
| Hard-constraint cases passed, n=100 | 0.57 | 1.00 | Generated constraints from represented fields only. |
| Constraint violations | 1 | 0 | Does not prove correctness for missing/unrepresented attributes. |
| Candidate Recall@5 | 0.7628 (legacy grader; not comparable) | 0.6841 (100 evaluable cases, corrected grader) | Provisional synthetic candidate coverage; corrected denominator uses all eligible IDs and only raw ranks 1–5. |
| Category scope / context / price ordering | Not comparable | 150/150; 100/100; 50/50 | Parser/tool harness, not response prose. |
| Alternative intent / candidate novelty | Not measured | 50/50 intent; novelty 0 cases | Intent recognition is not successful alternative recommendation. |
| Manually authored parser/retrieval dev set, n=200 | Direct-search relevant@1/.@5 .075/.090 | Post-category-browse rerun: tool relevant@1/.@5 .905/.925; category P@5 .897; product type 98%; intent 95.31%; negation 76.92%; comparison exact match 80%; case exact match 95% | Exposed development set, not blind; distinct from generated exact-match cases. Ten exact-case failures remain; see the new artifact. |

Latest corrected dev artifact: [`semantic_intelligence_v2_dev_rev1_post_grader_20261029.json`](artifacts/semantic_intelligence_v2_dev_rev1_post_grader_20261029.json). Historical `retail_ai_semantic_intelligence_v2_dev_rev2_20261028.json` used the invalid Recall@5 calculation and its value of 1.00 must not be used. The separate frozen run is [`semantic_intelligence_v2_post_grader_20261029.json`](artifacts/semantic_intelligence_v2_post_grader_20261029.json). Baseline and historical numbers are documented in [`retail_ai_semantic_intelligence_v2.md`](retail_ai_semantic_intelligence_v2.md).

Post-pagination rerun: [`semantic_intelligence_v2_final_20261029.json`](artifacts/semantic_intelligence_v2_final_20261029.json). On its separate frozen synthetic dataset, exact product R@1/R@5 was 1.00/1.00; category P@5 was 1.00; encoded-constraint cases passed 100/100 with zero violations; corrected candidate Recall@5 was 0.6878 over 98 evaluable cases and nonempty results were 0.98. Context retention, parser scope, category scope, price ordering, alternative-intent recognition, and first-item references passed their encoded checks. The artifact marks alternative candidate novelty as not evaluated. Deterministic p50/p95 latency was 89.508/149.740 ms in this run; these are regression diagnostics, not semantic-answer quality or LLM latency.

## 11. Ablation by dimension

| Dimension | Result | Decision |
| --- | --- | --- |
| Deterministic parser + catalog | Dev product R@1/R@5 and category P@5 are 1.00 on generated cases. | Keep as low-latency/evidence-grounded path; do not infer human quality. |
| LLM structured extraction | 20/60 pass; 34 provider errors; 6 schema/parse/incomplete responses. | Keep model as conversational tool agent; do not call structured extraction for every query. |
| BM25 vs E5-small vs RRF, runtime-catalog title-derived dev n=150 | On the same 3,081 accepted products: BM25 R@1/R@5 .9800/1.0000, MRR@5 .9867, nDCG@5 .9900; E5 .9667/.9800, MRR .9711, nDCG .9733; RRF .9733/.9933, MRR .9833, nDCG .9860. | BM25 leads this explicit title-resolution set; dense/RRF do not justify a default change. |
| BM25 vs E5-small vs RRF, previously inspected SKU-separated title variants n=360 | Same runtime catalog: BM25 R@1/R@5 .9889/1.0000, MRR@5 .9944, nDCG@5 .9959; E5 .9389/.9833, MRR .9595, nDCG .9656; RRF .9722/.9917, MRR .9819, nDCG .9845. | Exploratory only: this split is exposed and is not a blind or semantic-fit test. |
| Retrieval latency, local Windows CPU, refreshed 2026-10-29 | Dev p50/p95 ms: BM25 full rank 21.147/26.963; E5 embedding only 10.535/13.716; E5 full embedding+score+sort 24.281/27.931; RRF fusion 0.169/0.215. Exploratory n=360 p50/p95: 21.115/26.376; 10.665/13.713; 25.163/29.394; 0.163/0.213 respectively. | Compare full E5 against full BM25; embedding-only timing is not an end-to-end retrieval comparison. Machine-specific, not API/LLM latency. |
| Bounded in-memory caches | Deterministic ASGI p50 improved vs prior same-script run: approx 1,354→707 ms at C1, 7,541→3,855 ms at C5, 7,878→4,871 ms at C20. | Retain bounded caches, but remeasure on server deployment and real query traffic. |
| Soft-preference recovery | New unit tests prove explicit parser-supported preference survives an omitted LLM field and budget-only revision is not treated as preference. | Keep as a narrow generic state guard; post-fix live run passed but the model emitted the preference itself. |

Artifacts: [`generalized_retrieval_variants_runtime_dev_20261029.json`](artifacts/generalized_retrieval_variants_runtime_dev_20261029.json) and [`generalized_retrieval_variants_runtime_heldout_exploratory_20261029.json`](artifacts/generalized_retrieval_variants_runtime_heldout_exploratory_20261029.json). Both use the same validated runtime corpus per run, but the generated queries are catalog-title variants, not human semantic-need queries. The paired bootstrap uses 5,000 resamples over 50 dev product-connected clusters and 120 exploratory clusters; it is an uncertainty estimate for these exposed synthetic query sets, not an independent semantic result. On exploratory queries, dense E5 vs BM25 has Recall@1 delta −5.00 pp (95% CI −7.22 to −3.06 pp); RRF vs BM25 has −1.67 pp (−3.33 to −0.28 pp). These results do not justify changing the BM25 production default. FastEmbed 0.9.1 / ONNX Runtime 1.31.1 were installed into a one-off `uv run --with fastembed` environment; no project dependency/lockfile was changed. Cold index build including model initialization and embedding all 3,081 items took 92.1 s for dev and 92.6 s for the exploratory run; the FP32 vector payload was 4,732,416 bytes. RSS/peak memory and model-download time were not measured. Cross-encoder and large embedding reranking were not implemented because reviewed qrels, hardware profile, and expected benefit are missing.

## 12. Error analysis

1. The previous alternative metric was misleading: 48/50 turns reused an item; 42/50 repeated all five IDs. Session novelty tracking is implemented, but the synthetic conversation harness still does not grade session-visible candidate novelty.
2. Picnic probe: the model proposed drinks, but the required “snack không cay” group had zero verified candidates because the catalog lacks a spice attribute. The answer disclosed this and did not count a substitute in the total. This is grounded abstention, though the plan is incomplete.
3. The first picnic run omitted the follow-up preference; the second run's raw Qwen trace included it. The second response said “Mình đã ghi nhận thêm ưu tiên bạn vừa nêu,” preserved the budget and unverified-snack status, and did not claim the product meets the preference. The selected items remained unchanged because the catalog has no shareability evidence. Unit tests separately verify the parser guard when a model call omits the field.
4. “Best seller” cannot be inferred from catalog order or snapshot stock. The probe abstained because verified sales data is absent.
5. Shelf position is explicitly labeled a recommended planogram (`A03-B03-L03-P07`), while the source row records `C1-L2`; it is not represented as verified physical placement.
6. Human naturalness, dialect robustness, ASR transcript tolerance, useful substitutions, and multi-intent accuracy remain unmeasured independently.
7. The exposed 200-query parser rerun is recorded in `independent_consultation_v2_post_category_browse_20261029.json`: it still has 10 exact-case failures, including comparison/variant intent mismatches (V2-097, V2-137, V2-167, V2-197) and three exact-negation-span mismatches. The V2-137 API router now retrieves both grounded categories and asks for explicit products, but the parser's primary `product_type` field still follows the first operand and misses the benchmark's expected second operand. The new category-browse route is separately covered by API regressions. Negation-span failures preserve the excluded concept with a different span (`cay` vs `loại cay`, or include `rau`); they are not established product violations. These exposed labels require semantic audit rather than automatic code changes.
8. The runtime snapshot assigns both “Ếch làm sạch” and “Nhộng tằm” to the leaf `Cá nước ngọt tươi`. Category browse faithfully follows that stored path, so this upstream catalog-classification error can surface semantically wrong results even when the search tool and response match the snapshot. No product-specific filter was added; the source taxonomy/data needs review before claiming semantic accuracy for these categories.

## 13. End-to-end conversation examples

**Picnic:** “Picnic cho 4 người, khoảng 200 nghìn, snack không cay và nước uống.” The response grounds Aquafina 5L at 26,900đ from the 15/09/2026 snapshot, describes stock as not real-time, says the snack attribute is unavailable, and does not add to the cart. Good grounding; incomplete shopping coverage is expected from missing evidence.

**Follow-up:** “Ưu tiên món dễ chia cho mọi người…” The first live run repeated the same drink-only plan and failed to acknowledge the new preference. On the repeat, Qwen itself included the preference in tool arguments and the response acknowledged it while keeping the 26,900đ plan unchanged. Unit tests verify the backend also preserves a parser-supported preference if a model omits it, while keeping budget/party size and hard requirements intact. The repeat therefore verifies end-to-end acknowledgement, but not the omitted-field guard under live model output. The catalog has no verified shareability field, so the system does not claim the selected drink is shareable.

**Pantene:** Exact query returned snapshot price 169,900đ; shelf follow-up returned `A03-B03-L03-P07` explicitly as a proposed location, with physical placement unverified; bestseller follow-up abstained because sales data is missing.

See [`live_snapshot_provider_probe_after_preference_fix_20261028.json`](artifacts/live_snapshot_provider_probe_after_preference_fix_20261028.json) for post-fix per-turn inputs, outputs, tools, IDs, and safety checks. [`generalized_live_hard_cases_20261028.json`](artifacts/generalized_live_hard_cases_20261028.json) records the separate `/agent/respond` and SSE smoke. Do not treat HTTP 200s as high-quality human-graded answers.

## 14. Quality gates

| Gate | Result | Status |
| --- | --- | --- |
| Full backend/evaluation pytest suite | `uv run pytest -q --tb=short`: 1,294 passed in 174.64 s on the current uncommitted same-branch tree. Previous commit `350505e8adba722d6ea21766aa748fa59d987b79` passes GitHub Actions run [`40765305100`](https://github.com/chinhdz102/Retail-AI-/actions/runs/40765305100); CI for this continuation is pending. | Local pass; new diff awaits CI. |
| Focused conversation/session/catalog/evaluator tests | 379 passed in 27.44 s, including four product categories, second-item price follow-up, removal by reference/ordinal, ambiguous clarification, negation, cart invariance, and corrected Recall@5 cases. | Pass locally. |
| Ruff, compileall, and `git diff --check` | `uv run ruff check backend tests evaluation scripts`, `uv run python -m compileall -q backend tests evaluation scripts`, and `git diff --check` all pass. Git emits only expected Windows line-ending conversion warnings. | Pass locally. |
| Seeded catalog metamorphic invariants | 5/5 passed: punctuation/accent variants preserve 64 sampled SKUs; budget caps held across 17 retrievable scopes; brand exclusions held across 32 scopes; 32 unseen tokens returned no product; a newly added synthetic SKU was indexed without product-specific code. | Pass for deterministic catalog regressions only; not a relevance or human-quality score. |
| Runtime-catalog multi-system qrels exporter | Fresh export pooled BM25/E5/RRF for the same 150 development queries; two reviewer files contain 1,134 rows each, 5–14 candidates/query, all products in the accepted catalog, identical candidate membership with independent order, no source/rank leakage, and all grades/rationales blank with status `UNREVIEWED`. Forty dataset cases absent from the retrieval artifact were correctly skipped. | Pass for queue integrity only. Human relevance has not been judged; this is not an accuracy result. |
| Branch CI workflow | Prior run `40744779694` failed 3/1,238 tests on Linux because the committed planogram only matched the Windows raw-byte catalog hash. Commit `42479e7` adds a canonical-hash path and LF-checkout integration regression; run `40748503030` passes Ruff, all 1,241 tests, and compileall on Ubuntu 24.04. Run `40755659331` passes on code commit `89472bf`, run `40757067626` passes on docs commit `10c9f96`, and run `40759292984` passes on category-browse code `68da946`; later runs `40761103420`, `40761593020`, and `40765305100` also pass. | Pass, including latest code commit. |
| Vietnamese parser hot path | 50 authored development queries × 3 functions; baseline 564.280 ms first pass / 1,684.934 ms repeated; optimized 64.310 ms / 190.479 ms; 150/150 outputs identical. | 8.846× repeated-path speedup on this machine only; not an end-to-end latency or blind semantic-accuracy score. |
| Synthetic product/category/constraint checks | 100/100 exact-product and category checks; zero encoded-constraint violations. Corrected candidate Recall@5 is 0.6841 on dev_rev1 (100 evaluable) and 0.6878 on the separate frozen file (98 evaluable); 2/100 frozen hard-constraint cases have no eligible item. | Pass for represented deterministic invariants; recall is provisional and synthetic. |
| Post-pagination frozen benchmark | 300 generated queries + 50 synthetic dialogues; corrected candidate R@5 0.6878, zero encoded-constraint violations, and novelty status not evaluated. | Development regression only; old 0.98 value used the faulty grader and is superseded. |
| Live API grounding invariants | Post-fix run: 5/5 turns HTTP 200; 5/5 automated grounding checks passed; no fallback used. | HTTP/grounding gate only; not human-graded. |
| Prompt-injection and unauthorized-write regressions | Covered by API/support tests for false price changes, secret requests, fake role/tool, payment, and ticket boundaries. | Regression coverage; not a penetration test or formal security certification. |
| Human qrels, naturalness, helpfulness, abstention calibration | Not completed. | Fail release gate. |
| Concurrency capacity / deployment SLA | Not measured; local in-process smoke degrades at C5/C20. | Fail production release gate. |
| STT/TTS voice quality | Not measured in this text-only evaluation. | No voice-quality claim. |

## 15. Latency/RAM/CPU

Latest [`api_runtime_load_sanity_20261029.json`](artifacts/api_runtime_load_sanity_20261029.json), deterministic catalog-only in-process ASGI; 20 actual requests per level, warm-up excluded, and observed concurrency verified:

| Configured concurrency | Requests / observed in flight | p50 | p95 | p99 / max | Throughput | HTTP errors / identity-price mismatches |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 20 / 1 | 130.922 ms | 169.333 ms | 172.410 / 172.410 ms | 7.314 req/s | 0 / 0 |
| 5 | 20 / 5 | 515.055 ms | 672.084 ms | 679.909 / 679.909 ms | 7.668 req/s | 0 / 0 |
| 20 | 20 / 20 | 2,552.342 ms | 2,563.249 ms | 2,563.859 / 2,563.859 ms | 7.750 req/s | 0 / 0 |

A separate single-request `tracemalloc` probe measured Python heap current 323,238 B and peak 704,006 B. Repository construction took 538.707 ms; excluded warm-up response took 297.247 ms. The harness excludes network, browser, TLS, reverse proxy, server worker pools, provider calls, STT, and TTS. Twenty requests per level are still a smoke profile, not a capacity or SLA claim. C20 p50 remains about 2.55 s, unsuitable for a responsive production chat target.

The 2026-10-28 five-request run is retained for audit but is not directly compared: it did not send enough work to reach C20 and excluded neither a dedicated warm-up nor environment metadata. This run corrects those measurement limits.

The separate deterministic V2 run measured p50 105.075 ms and p95 203.005 ms over 300 catalog-only cases. It excludes Groq and should not be compared with end-user or provider latency; it is separate from the five-request ASGI concurrency probe above.

## 16. Provider reliability

The post-fix live probe used Groq `qwen/qwen3.8-27b` for two turns and local deterministic/catalog paths for the other three. All five returned HTTP 200, all five passed automated grounding checks, and both model calls succeeded with the primary credential; no fallback was recorded. The two model turns consumed 7,599 reported tokens (3,344 + 4,255). This is a tiny single-run sample and does not estimate outage/fallback reliability or production latency distributions.

Per-turn API latency in the post-fix probe: 2,830 ms and 2,303 ms for the model-backed picnic turns, 293 ms, 222 ms, and 111 ms for catalog/local turns. The provider-call timings were 2,443 ms and 1,968 ms. These are one-run timings, not provider p50/p95. Credentials were configured, but secret values were excluded.

## 17. Security regression

Automated regression coverage checks that user-provided injection does not override catalog price, expose secrets, fabricate an admin role/tool, mutate payment/cart state without authorization, or create an unrelated support ticket. Relevant tests include `tests/test_retail_api.py` injection-context cases and `tests/test_support.py` ticket-policy cases.

The checks run in the full suite; they do not establish resistance to every prompt-injection technique, indirect document attack, model/provider compromise, or cross-tenant deployment issue. Tool authorization, session isolation, backend validation, and data provenance remain the primary security boundaries.

## 18. Human review status

**No independent human qrels or conversation-quality review was completed.** The 300-query / 50-conversation semantic benchmark is synthetic and development-facing. A split audit found 280/300 identical `(case_id, query)` pairs and 50/50 matching conversations between the two V2 files; both were made from the same seed. The separately titled generalized “heldout” set is SKU-disjoint but uses product-name-derived templates, while its taxonomy and conversation templates overlap with development. None qualifies as a blind semantic-quality set. The two-case live probe was manually authored and inspected by the implementer, so it is diagnostic only. A matched retrieval run pools BM25, E5-small, and RRF over the same 150 development queries and exact runtime-accepted catalog. Two blinded reviewer queues each contain 1,134 ungraded rows; candidate membership matches while ordering differs. Every label and rationale remains blank and `UNREVIEWED`. No human answer-quality rubric, reviewer agreement, or adjudication is available. These are review queues, not completed human evaluation or relevance/quality scores.

Annotation schema, guidance, and exporter are present under `evaluation/schemas/`, `evaluation/review/`, and `evaluation/vietnamese_product_qrels_annotation_guide.md`. Queue files: [`annotator A`](../../evaluation/review/vietnamese_qrels_runtime_catalog_dev_20261029_annotator_a.csv), [`annotator B`](../../evaluation/review/vietnamese_qrels_runtime_catalog_dev_20261029_annotator_b.csv), with evaluator-only metadata in [`manifest`](../../evaluation/review/vietnamese_qrels_runtime_catalog_dev_20261029_evaluator_only_manifest.json). The next review should use two independent annotators for Exact/Substitute/Complement/Irrelevant labels, adjudicate disagreements, and freeze a separately authored blind set before tuning against it.

## 19. Remaining limitations

- Catalog freshness is 2026-09-15; stock and price may have changed.
- Descriptions are sparse/repetitive; many efficacy, flavor, spice, audience, compatibility, material, and use attributes are unavailable.
- No verified sales, rating, or popularity data; bestseller/tastiest claims must abstain.
- Shelf planogram is synthetic/recommended and not proof of physical placement.
- Search metrics do not validate suitability for implicit needs, accepted substitutes, complements, or multi-product tasks.
- Both new retrieval query sets are catalog-derived title variants. The 360-case exploratory set has previously been inspected; neither retrieval artifact is a blind holdout or human relevance benchmark.
- CPU-only E5 indexing has a material cold-start cost (~65–74 s in these runs); model download time and peak RSS were not measured. The retrieval experiment is not integrated into production runtime.
- The seeded metamorphic suite verifies repeatable repository/tool invariants against this snapshot; it does not measure semantic relevance, naturalness, or recommendation suitability. Its one synthetic catalog row is only a generic index-rebuild check.
- Synthetic development cases have leakage risk and narrow language coverage; accentless text, typos, dialects, ASR errors, and long multi-intent conversations need independent review.
- Alternative SKU novelty is not measured by the parser benchmark; current load test is too small for capacity estimates.
- No actual browser/network E2E or voice/STT/TTS benchmark is part of this report.
- Post-fix preference handling now has focused unit coverage and one successful live provider re-run; repeated sessions and broader preference types still need independent coverage.
- Product knowledge now models evidence/provenance, but the current catalog has no independently verified taste, efficacy, usage, or suitability facts. Knowledge-tool availability cannot fill those data gaps.
- Runtime snapshot audit on 3,081 accepted products found 0 non-null ingredient fields, 0 non-null spice fields, all 3,081 shelf assignments sourced from `RECOMMENDED_PLANOGRAM`, and 2,844 descriptions equal to the product name. The evidence envelope has no `source_url` or reviewer field yet, and no manufacturer facts are loaded into the runtime tool. This is why correct abstention still cannot support much substantive suitability advice.
- A source audit of Pantene, NESCAFÉ, Vinamilk, Coca-Cola Zero, and Sunlight found no candidate that met the exact-SKU plus authoritative-source bar for a safe enrichment pilot. Product-line similarity is not sufficient to verify a specific barcode/package; the knowledge loader and reviewer workflow remain future work.
- Alternative page-walking is unit and repository tested; actual novelty over multi-turn live API sessions remains ungraded by the frozen benchmark.

## 20. Release decision

**NOT READY for production. Demo-only with an operator aware of snapshot staleness and unknown product attributes.**

The implementation passes deterministic development regressions and guarded grounding examples, but lacks evidence for the core release claim: accurate and natural advice on independently written, unseen Vietnamese requests across the whole catalog. Production should be reconsidered after human-reviewed blind evaluation, acceptable API load profile, reliable current store data, and successful live preference/session revalidation.

## 21. Reproduction commands

Run from repository root:

```powershell
uv run pytest tests -q --tb=short
uv run ruff check backend tests evaluation
uv run pytest tests/test_catalog_metamorphic_invariants.py -q --tb=short
uv run pytest tests/test_semantic_intelligence_v2_benchmark.py -q --tb=short
uv run pytest tests/test_retail_agent.py tests/test_shopping_mission.py -q --tb=short
uv run python -m evaluation.semantic_intelligence_v2_benchmark `
  --dataset evaluation/cases/retail_ai_semantic_intelligence_v2_dev_rev1_20261028.json `
  --output docs/reports/artifacts/retail_ai_semantic_intelligence_v2_dev_rev2_20261028.json
uv run python evaluation/semantic_intelligence_v2_benchmark.py `
  --output docs/reports/artifacts/semantic_intelligence_v2_postfix_20261029.json
uv run python -m evaluation.api_runtime_load_sanity `
  --requests-per-concurrency 20 `
  --output docs/reports/artifacts/api_runtime_load_sanity_20261029.json
uv run python -m evaluation.vietnamese_parser_hot_path_benchmark `
  --output docs/reports/artifacts/vietnamese_parser_hot_path_20261029.json
uv run --with fastembed python -m evaluation.generalized_retrieval_variants `
  --cases evaluation/cases/generalized_catalog_consultation_dev_v1.jsonl `
  --output docs/reports/artifacts/generalized_retrieval_variants_runtime_dev_20261029.json
uv run --with fastembed python -m evaluation.generalized_retrieval_variants `
  --cases evaluation/cases/generalized_catalog_consultation_heldout_v1.jsonl `
  --output docs/reports/artifacts/generalized_retrieval_variants_runtime_heldout_exploratory_20261029.json
uv run python -m evaluation.export_vietnamese_qrels_review `
  --dataset evaluation/cases/generalized_catalog_consultation_dev_v1.jsonl `
  --results docs/reports/artifacts/generalized_retrieval_variants_runtime_dev_20261029.json `
  --output-prefix evaluation/review/vietnamese_qrels_runtime_catalog_dev_20261029 `
  --catalog data/winmart_products.json `
  --split development `
  --seed 20261029 `
  --max-candidates-per-query 15
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
