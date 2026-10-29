# Research notes: Vietnamese product retrieval and conversational consultation

**Reviewed:** 29 October 2026
**Code revision:** `f61e39e40671f1912845856d6d25610a58f8d551`
**Scope:** research requested by the Retail-AI production-quality roadmap. The references below inform experiments; they do not validate Retail-AI or replace evaluation on this catalog.

## Decision summary

1. Keep the deterministic catalog route and hard identity/brand/category/price/negation filters as the safety baseline. A dense model must never override an explicit product identity or a hard constraint.
2. Do not enable embeddings, a cross-encoder, a vector database, or a new recommendation rule in production based only on published benchmark scores. The local catalog has 3,081 accepted products, but many product attributes needed for suitability advice are absent; the latest review packet also has no human judgments.
3. Compare the production tool path, BM25, multilingual E5-small, and a rank-fused variant against the same *human-judged, natural-need* candidate pool. Preserve exact/substitute/complement/irrelevant grades and separate hard-fit violations. Promote a model only if paired query-level results improve with acceptable latency and no rise in critical wrong-product/constraint errors.
4. Use clarifying questions only to resolve information that changes the candidate set. When source fields cannot verify a requested property, say so and avoid asserting suitability. Keep session constraints structured and explicit.
5. Do not add Qdrant or Milvus for a 3,081-row snapshot. A rebuildable in-process or PostgreSQL sidecar index is sufficient for an experiment; benchmark memory, update consistency, and latency before selecting an index.

## 1. Vietnamese retrieval benchmark: ViRE (EACL Findings 2026)

**Problem and method.** ViRE compares lexical (BM25/TF-IDF), dense, learned sparse, late-interaction, and hybrid retrieval through a shared load/normalize/sample/retrieve/fuse/evaluate workflow. The repository reports six Vietnamese domains and ten datasets; customer-support data CSConDa and education data EduCoQA are introduced from conversations. Its CLI fixes normalization, candidate sets, fusion, cutoffs, and seeds to make comparisons reproducible. [Paper](https://aclanthology.org/2026.findings-eacl.110/) · [Code and benchmark README](https://github.com/longstnguyen/ViRE)

**Reported results and limits.** On the standardized 1,000-pair CSConDa subset, reported MRR@10 is 22.99 for BM25, 41.06 for the best dense model, and 43.60 for the best hybrid. Across the ten datasets the best hybrid exceeds the best dense row in the paper's table. The authors also report that conversational customer-support retrieval is much harder than legal retrieval, and that parameter count does not consistently predict quality. These are passage-retrieval results from QA/customer support, not product identity, package variants, or shopping suitability. CSConDa and EduCoQA are CC BY-NC 4.0; do not copy either into a commercial training/evaluation corpus without resolving license scope. [Results and license](https://github.com/longstnguyen/ViRE#results)

**Fit and decision.** Adopt ViRE's matched-method comparison, fixed preprocessing, query-level error files, reproducibility metadata, and per-domain reporting. Do not import its dataset as product-search gold or copy its full toolkit. Retail-AI's 3,081-row catalog is small enough that BM25 and dense retrieval can be measured locally without a separate vector service. A Vietnamese-specific encoder remains a candidate, but its score on another domain is only a prior.

## 2. Conversational product search: ProductAgent / PROCLARE (EMNLP Industry 2025)

**Problem and method.** ProductAgent handles an underspecified shopping request through iterative clarification and retrieval. It uses structured dialogue memory, a product-candidate summary, and a hybrid symbolic+dense retrieval loop; clarification options are based on observed catalog facets. The paper introduces PROCLARE, an LLM user simulator and 2,000 generated sessions; its reported retrieval metrics improve as turns add constraints. [Paper](https://aclanthology.org/2025.emnlp-industry.25/)

**Strengths and limits.** The useful ideas for this codebase are explicit state, candidate evidence before asking a question, and measuring whether a clarification changes the next result set. A simulator makes iterative experiments cheap. It is still a simulator and generated sessions are not independent human preference judgments; the paper's abstract alone does not establish that every simulated preference is realistic. The full architecture is designed for a larger product schema and database than this 3,081-product snapshot.

**Fit and decision.** Reuse the *principles*, not the full autonomous loop: keep the existing session state and deterministic tools; ask one short question only when product type, budget scope, or a decision-driving facet is genuinely ambiguous. For experiments, score state retention, constraint application, candidate coverage, and clarification usefulness separately. Avoid letting an LLM invent product facets: the local snapshot lacks verified ingredients/spice for many products.

## 3. Product relevance labels: Amazon ESCI

**Problem and schema.** ESCI distinguishes **Exact**, **Substitute**, **Complement**, and **Irrelevant** query-product relationships. It provides query/product fields and human judgments; its tasks include ranking, multiclass relevance classification, and substitute identification. [Dataset paper](https://arxiv.org/abs/2206.06588) · [Official schema and repository](https://github.com/amazon-science/esci-data)

**Strengths and limits.** This is the right conceptual vocabulary for separating “same category” from “serves the request.” It is multilingual English/Spanish/Japanese, not Vietnamese. An ESCI grade cannot establish a health, dietary, safety, or use-case claim, and relevance grades must not erase hard constraints (e.g. “không cay”, “không sữa”, exact size).

**Fit and decision.** Keep graded qrels rather than converting all near matches to binary relevance. Add independent hard-fit dimensions for product type, category, identity/variant, brand, budget/price unit, negative constraints, and evidence. A substitute is a candidate only after a reviewer says it preserves the customer's central need; a complement is not a substitute and should never silently replace a requested item.

## 4. Negation-aware product search (The Web Conference 2023)

**Problem and method.** Merra et al. describe how product engines can return the unwanted feature for queries such as “cover without ring”. They propose ND4Q to detect negation intent and FIP to filter incompatible products; the Amazon Science summary reports over 95% negation-detection accuracy and a +9.41% nDCG@16 improvement for queries beginning with “without” on their evaluation. [Publication and results](https://www.amazon.science/publications/improving-the-relevance-of-product-search-for-queries-with-negations)

**Strengths and limits.** It supports modeling negation explicitly and auditing it as a critical failure, rather than treating “without X” as another soft token. The published English product data and model scores do not prove Vietnamese negation-scope accuracy. A rule that removes a negated phrase can also corrupt mixed clauses (e.g. “không lấy Brand A, ưu tiên loại không đường”).

**Fit and decision.** Keep negation as a structured constraint with source span and clause scope. Apply catalog-backed hard exclusion before ranking, and do not infer an absent product attribute as true. Add paired minimal tests that differ only by polarity and test punctuation, coordination, colloquial Vietnamese, and ASR transcription variants. No generic text rewrite should be allowed to drop a hard negative.

## 5. Clarification and abstention: URASys (Findings IJCNLP-AACL 2025)

**Problem and method.** URASys combines lightweight query decomposition, FAQ and document retrieval, a manager agent, and a “Just Enough” response policy: retrieve evidence, clarify when required context is missing, or explicitly indicate an unanswerable request. Its public repository uses two complementary indexes (contextualized document chunks and generated/expanded FAQ variants), with BM25 plus dense retrieval. [Paper](https://aclanthology.org/2025.findings-ijcnlp.27/) · [Code](https://github.com/ura-hcmut/URASys)

**Strengths and limits.** The ambiguity/unanswerable distinction maps well to Retail-AI's anti-hallucination requirements. However, URASys is an academic-advising/document QA system. Its default walkthrough uses external model APIs and Milvus Lite; copying that storage and agent topology would add dependencies without evidence for a 3,081-product catalog.

**Fit and decision.** Adopt the response policy, not the Milvus stack: direct answer if the tool returns verified facts; one targeted clarification if ambiguity changes the search; abstain from unsupported suitability, popularity, stock, or policy claims. Keep current local snapshot/PostgreSQL interfaces. Do not add FAQ paraphrase expansion for product records until judged recall shows that lexical aliases are insufficient.

## 6. Leakage-resistant conversation evaluation: PEPPER (Findings EMNLP 2025)

**Problem and method.** PEPPER argues that target-aware user simulators can leak the desired SKU into a conversation and let a recommender “guess” the answer; target-item recall also misses whether preferences were elicited correctly along the way. It proposes target-free simulators whose preferences unfold through interaction, with intermediate preference-elicitation measures. [Paper](https://aclanthology.org/2025.findings-emnlp.1067/)

**Strengths and limits.** This directly informs Retail-AI's generated conversation tests. A target-free simulator still does not replace human review, and model-generated user turns can encode assumptions or unnatural preferences. The safest use is development diagnostics, with synthetic provenance and no blind-quality claim.

**Fit and decision.** The existing catalog-derived title variants and template conversations are exposed development data, not blind evidence. Future conversation generation must keep gold product IDs and evaluator labels out of the user prompt; success must be graded against all preserved constraints and accepted product sets, not one planted SKU. Human qrels/review remain required for validation and release.

## 7. Embedding candidates and reranking

### Multilingual E5

The multilingual E5 report describes small/base/large models trained by multilingual contrastive pretraining and supervised fine-tuning with mined hard negatives; it uses the `query:` / `passage:` input convention for retrieval. Its small model trades quality for lower inference/storage cost. [Technical report](https://arxiv.org/abs/2402.05672) · [Official model card](https://huggingface.co/intfloat/multilingual-e5-small)

**Retail-AI fit:** it is the lowest-risk first dense candidate because the repository already has an isolated FastEmbed/ONNX E5-small experiment and the 3,081-item corpus needs only about **4.7 MiB** of float32 vectors at 384 dimensions. That memory calculation is `3,081 × 384 × 4`; it excludes model/runtime memory. Existing evidence is title-derived identity matching, not semantic recommendation. Keep it offline/optional until natural-need qrels show a lift.

### BGE-M3 and FlagEmbedding

BGE-M3 trains dense, learned-sparse, and multi-vector representations with self-distillation, supports over 100 languages and 8,192-token input, and has 1,024-dimensional dense output. FlagEmbedding provides model loading, sparse/dense/ColBERT-style use, fusion, and cross-encoder rerankers; its README explicitly recommends evaluating a reranker on the retrieved top-k candidates. [Paper](https://arxiv.org/abs/2402.03216) · [Model card](https://huggingface.co/BAAI/bge-m3) · [FlagEmbedding code](https://github.com/FlagOpen/FlagEmbedding) · [Reranker inference guide](https://github.com/FlagOpen/FlagEmbedding/blob/master/examples/inference/reranker/README.md)

**Retail-AI fit:** multilingual robustness and one model supporting several retrieval modes are attractive. The 1,024-dimensional representation costs about 12 MiB for 3,081 float32 vectors before index overhead; the model itself is much larger (the official checkpoint is multi-gigabyte). Sparse and multi-vector indexing do not justify extra runtime/storage complexity at this catalog size without qrels evidence. Compare dense and any hybrid separately; do not assume all M3 modes improve Vietnamese retail relevance.

### Qwen3-Embedding / Reranker

The Qwen3 series includes a 0.6B embedder and reranker, plus 4B/8B variants; the 0.6B embedder is 1,024-dimensional, supports 100+ languages, instructions, and 32k tokens. Its model card recommends task instructions and reports a general 1–5% improvement from instructions in its evaluated tasks; this is not a Retail-AI result. The project supplies model code/examples and uses Apache 2.0 licensing. [Paper](https://arxiv.org/abs/2506.05176) · [Official model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) · [Code](https://github.com/QwenLM/Qwen3-Embedding)

**Retail-AI fit:** stronger language reasoning may help natural queries but the 0.6B parameter model is much heavier than the current E5-small ONNX path and would need a measured CPU/GPU/runtime deployment choice. Do not use model-card leaderboard results as ranking evidence. Consider only after qrels, resource measurement, and an inference backend are available. Keep instructions constant and English if evaluated because that matches the model card's multilingual guidance.

### Vietnamese-specific embedding

`AITeamVN/Vietnamese_Embedding_v2` is fine-tuned from BGE-M3 on approximately 1.1 million Vietnamese query-positive-negative triplets; its published evaluation is on Zalo legal retrieval, not retail, and its card lists 1,024 dimensions and 0.6B parameters. [Model card](https://huggingface.co/AITeamVN/Vietnamese_Embedding_v2)

**Retail-AI fit:** it is a relevant candidate for Vietnamese informal phrasing, but its legal-domain score cannot be transferred to catalog products. The model is F32 and not currently part of the backend runtime. Benchmark it on natural-need product qrels; do not install it into production merely because it is Vietnamese-specific.

### Cross-encoder and CPU/ONNX

Sentence Transformers documents the two-stage pattern: retrieve a small candidate set, then score each `(query, product)` pair with a cross-encoder. It warns that reranking candidates cannot recover a relevant item missed by the first stage, and recommends realistic evaluation where positives are reranked only when actually retrieved. It also supports ONNX Runtime as an inference backend. [Retrieve & rerank example](https://github.com/huggingface/sentence-transformers/blob/main/examples/sentence_transformer/applications/retrieve_rerank/README.md) · [Reranking evaluator guidance](https://github.com/huggingface/sentence-transformers/blob/main/skills/train-sentence-transformers/references/evaluators_cross_encoder.md) · [ONNX efficiency guide](https://github.com/huggingface/sentence-transformers/blob/main/docs/sentence_transformer/usage/efficiency.rst)

**Retail-AI fit:** if independent judgments show candidate relevance is the bottleneck, test a cross-encoder only on the top 10–20 candidates after hard filters. This improves ordering, not candidate recall or factual catalog knowledge. Count all pairs in latency and test batch size on the target CPU/GPU. Do not claim ONNX speedup without a before/after measurement on this machine.

## 8. Evidence from the current codebase and next experiment

The current production tool is `execute_catalog_tool(search_products)` plus `SnapshotProductRepository`; the BM25/E5/RRF research experiment previously bypassed that path. Commit `f61e39e` adds a separate snapshot benchmark of the actual deterministic tool path and includes it in the offline retrieval ablation. Its 150 development queries are 50 catalog-title conversational variants, 50 accentless variants, and 50 single-typo variants. It achieved identity Recall@1/5 **100%** and tool-path p50/p95 **56/92 ms**, but this says nothing about finding suitable products from a need such as “đồ ăn nhẹ không cay cho trẻ”.

The report and catalog audit show missing suitability attributes (including non-null ingredient/spice fields) and many descriptions that repeat product titles. Current reviewer queues have zero completed labels, and no blind validation/test dataset is registered. Therefore the evidence does not justify a ranking-model change.

**Next controlled experiment:**

1. Obtain independently authored Vietnamese need queries; do not expose target product IDs/names to the user simulator. Include natural wording, typos/no-diacritic input, multi-intent requests, budget/brand/negation, package-size ambiguity, impossible needs, and questions requiring clarification.
2. Run the exact same catalog/snapshot and query set through production tool, BM25, multilingual E5-small, and BM25+E5 RRF. Preserve the production hard filters before every reranker.
3. Pool candidate IDs from all methods. Have two reviewers independently grade Exact/Substitute/Complement/Irrelevant and hard-fit fields; adjudicate disagreements. Keep development, validation, and sealed blind data separate.
4. Report nDCG@5/10, MRR, exact/substitute/complement rates, wrong-type rate, hard-constraint violations, candidate recall, abstention/clarification quality, paired query-level confidence intervals, model load/index cost, and p50/p95/p99 cold/warm latency.
5. Test a cross-encoder only if the judged candidate pool shows ranking errors after retrieval; it cannot repair missing candidate recall or absent product facts.

Until that experiment is completed, keep semantic retrieval experimental and release state **demo-only / not production-ready**.
