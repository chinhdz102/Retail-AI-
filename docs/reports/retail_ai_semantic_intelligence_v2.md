# Retail-AI — Generalized Semantic Intelligence V2

**Trạng thái:** triển khai và đánh giá provisional; chưa được annotator độc lập xác nhận
**Ngày:** 28/10/2026
**Branch:** codex/retail-text-quality-v4-20261019
**Commit nền:** f6403da9b59fa341440cd86dec00dc32d72f1bee
**Working tree:** đang có thay đổi chưa commit; lượt này không commit/push.

## 1. Tóm tắt điều hành

V2 gia cố pipeline tư vấn catalog: phân tích câu tiếng Việt, resolve category và tên hàng, lọc điều kiện xác minh được, giữ ngữ cảnh đa lượt và nối follow-up với SKU đã hiển thị. Đã sửa ở parser, retrieval/tool, repository và session API; không thêm nhánh theo SKU hoặc câu benchmark.

| Chỉ số provisional | Baseline | V2 |
| --- | ---: | ---: |
| Product Recall@1 / @5 (100 truy vấn) | 32% / 37% | 100% / 100% |
| Category Precision@5 (100 truy vấn) | 80% | 100% |
| Parser category accuracy | 95% | 100% |
| Hard-constraint case hợp lệ hoặc abstain đúng | 57% | 100% |
| Constraint violations | 1 | 0 |
| Candidate Recall@5 | 76,28% | 98% |
| Giữ category/scope follow-up | — | 100/100 |
| Sắp giá tăng dần | — | 50/50 |
| Reference đến sản phẩm đầu tiên | — | 50/50 parser-only; API smoke xác minh 1 session |

Đây là kết quả trên dữ liệu template tự sinh từ catalog và đã được dùng trong phát triển, không phải độ chính xác tư vấn ở người dùng thật. Chưa đánh giá human naturalness, usefulness, công dụng sản phẩm, hallucination rate, phương ngữ hay audio.

Full test suite: **1.203 passed trong 704,98 giây**, chạy sau các thay đổi production. Sau chỉnh eval grader, **5 focused tests passed**, Ruff toàn repo và benchmark cuối cũng qua. Chi tiết và giới hạn ở dưới.

## 2. Baseline và môi trường

- Branch: codex/retail-text-quality-v4-20261019; base/HEAD tại thời điểm chạy là f6403da9b59fa341440cd86dec00dc32d72f1bee.
- Windows local, Python 3.13+, dependency theo uv. Artifact không ghi CPU model, RAM vật lý hay GPU.
- Catalog: data/winmart_products.json, SHA-256 a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6.
- Snapshot WinMart ngày 15/09/2026. Groq credentials hiện diện khi chạy ablation/live smoke; không ghi secrets vào artifact.
- Working tree dirty, chưa commit. Artifact mới lưu SHA-256 của diff/status.
- Log test cũ trong repo ghi nhiều số khác nhau; lượt xác minh lại của task này là 1.203 passed/704,98s. Không lấy các số 1.107/1.175/1.082 lịch sử để thay thế.

## 3. Root cause analysis

| Lớp | Nguyên nhân | Thay đổi |
| --- | --- | --- |
| Parsing | Cụm khẩu ngữ mở đầu bị dính vào tên hàng; raw query có thể lấn query đã chuẩn hóa. | Chuẩn hóa câu và tách intent/type/reference trước khi gọi catalog tool; dùng query đã hiểu ở deterministic response. |
| Taxonomy | Từ như “hướng”, “mùi” trong tên category bị tách thành attribute; alias ngắn có thể lấn nhãn đầy đủ. | Ưu tiên cụm taxonomy hoàn chỉnh/lá; dùng alias khi nó làm rõ category cha/audience. |
| Fuzzy retrieval | SequenceMatcher so quá nhiều token/candidate gây latency và có thể để tên gần giống lấn exact identity. | Giảm so sánh bằng lọc token/literal phrase, quick-ratio và cache vocabulary; giữ exact identity. |
| Repository | Term evidence có thể gọi lặp theo từng token. | Gom/batch evidence cho snapshot và PostgreSQL. |
| Constraints | Category, budget, brand loại trừ và product reference không luôn nằm cùng scope. | Biểu diễn thành trường riêng, lọc bằng dữ liệu catalog trước khi dựng câu trả lời. |
| Session | Nhánh deterministic không lưu candidate list vừa hiển thị; “giá loại đầu tiên” không xác định được ID. | Lưu candidate theo session và resolve ordinal; kiểm tra ID/giá qua API. |
| Timeout | Retry sớm có thể nhân đôi request catalog nặng. | Chờ một lần lâu hơn thay vì retry sớm; giảm tải trùng nhưng request chậm có thể chờ tới timeout. |
| Grader | Dò bất cứ chữ “khác” nào làm nhãn “Các loại bột khác” thành alternative false positive. | Grader loại nhãn category gold khỏi query trước khi dò intent; thêm 5 focused tests. Đây là lỗi grader, không phải parser. |

## 4. Nghiên cứu paper và quyết định

| Nghiên cứu | Rút ra và giới hạn | Quyết định |
| --- | --- | --- |
| [Which Works Best for Vietnamese? — EACL 2026](https://aclanthology.org/2026.findings-eacl.110/) | So sánh lexical, sparse, late-interaction, dense và hybrid trên nhiều IR task tiếng Việt; hiệu quả phụ thuộc domain/dataset. | Giữ exact/lexical baseline; đo trên catalog, không mặc định embedding thắng. |
| [ProductAgent — EMNLP Industry 2025](https://aclanthology.org/2025.emnlp-industry.25/) | Tư vấn sản phẩm dùng structured state, retrieval và clarification; setup mô phỏng/target-conditioned hạn chế suy rộng tới khách thật. | Giữ scope và hỏi làm rõ; không tuyên bố tái lập agent loop/paper score. |
| [Hint-Augmented Re-ranking — IJCNLP-AACL 2025](https://aclanthology.org/2025.ijcnlp-short.19/) | Tách tín hiệu xếp hạng như giá thành hints; reranking cần query-product labels. | Price criteria được tách riêng; chưa train reranker vì thiếu nhãn suitability. |
| [When in Doubt, Ask First — Findings IJCNLP-AACL 2025](https://aclanthology.org/2025.findings-ijcnlp.27/) | Làm rõ/abstain khi retrieval/evidence không đủ; kết quả phụ thuộc domain. | Không đoán sales/rating/công dụng; chưa tuyên bố confidence calibration. |
| [PEPPER — Findings EMNLP 2025](https://aclanthology.org/2025.findings-emnlp.1067/) | Target leakage trong user simulator có thể làm recommender benchmark đẹp giả tạo. | Phân loại benchmark là synthetic/provisional; cần câu độc lập và blind review. |
| [Retrieval-Augmented Spelling Correction for E-Commerce — 2024](https://aclanthology.org/2024.emnlp-industry.7/) | Dùng candidate catalog để sửa typo nhưng giữ brand/tên lạ. | Fuzzy correction chỉ dựa candidate trong catalog, không dùng từ điển/LLM để tự đoán brand. |
| [BGE-M3](https://arxiv.org/abs/2402.03216), [Qwen3 Embedding](https://arxiv.org/abs/2506.05176) | Đa ngôn ngữ/dense-sparse-multivector; không bảo đảm hiệu quả hoặc tài nguyên trên corpus này. | Chưa đưa vào production do thiếu nhãn in-domain và profile RAM/cold start. |
| [Amazon Shopping Queries / ESCI](https://www.amazon.science/code-and-datasets/shopping-queries-dataset-a-large-scale-esci-benchmark-for-improving-product-search) | Exact/Substitute/Complement/Irrelevant hữu ích cho nhãn product relevance; dữ liệu Amazon/English không khớp WinMart/Vietnamese. | Dùng làm ý tưởng schema relevance tương lai, không nhập gold label. |
| [VN-MTEB](https://leaderboard.mteb.org/benchmark/VN-MTEB%20%28vie%2C%20v1%29) | Đo embedding tiếng Việt tổng quát, không phải retail relevance. | Không lấy leaderboard score làm accuracy của Retail-AI. |

## 5. Kiến trúc trước/sau

**Trước:** câu user + history thô → heuristic intent/query nguyên văn → lexical search → response trên candidate có thể sai scope.

**Sau:** câu user + history giới hạn → chuẩn hóa và QueryUnderstandingResult → exact identity/taxonomy → candidate retrieval → lọc hard constraints có evidence → lưu candidate vào session → trả lời grounded hoặc hỏi làm rõ.

Đây là parser deterministic kết hợp tool/catalog grounded. Agent endpoint vẫn có thể dùng Groq cho hội thoại; parser structured LLM mới chỉ chạy ablation, chưa cắm vào fast path.

## 6. Module thay đổi

- backend/services/query_understanding.py: intent, product type/reference, use-case, negative/budget/quantity/ranking, ambiguity, comparison và follow-up; ưu tiên taxonomy đầy đủ.
- backend/services/vietnamese_intent.py: chuẩn hóa câu tiếng Việt/khẩu ngữ.
- backend/services/catalog_preferences.py, backend/services/shopping_mission.py: giữ/cập nhật preference và nhu cầu qua các lượt.
- backend/services/retail_tools.py: query cleanup, category evidence, budget/brand/negative filtering, fuzzy safeguards.
- backend/services/retail_agent.py: timeout catalog tool để tránh retry chồng tải.
- backend/products/snapshot_repository.py và postgres_repository.py: term evidence theo batch.
- backend/api/main.py: deterministic query cleanup, candidate list theo session và ordinal reference có ID/giá.
- evaluation/semantic_intelligence_v2_benchmark.py, evaluation/build_semantic_intelligence_v2_frozen.py: dataset, benchmark và grader.
- evaluation/generalized_live_consultation_smoke.py: API/SSE smoke và invariant.
- tests/: parser, repository, tools, API, shopping mission và grader regressions.

Không thêm dependency nặng vào runtime production.

## 7. Thiết kế schema

QueryUnderstandingResult hiện có: intent, secondary_intents, product_type/product_types/product_reference, use_case, hard_constraints, negative_constraints, soft_preferences, budget_vnd/scope/operator, quantity, brand_preference, comparison_scope/targets, conversation_reference, ranking_criterion, ambiguity, confidence, clarification_needed. Pydantic strict/frozen từ chặn field lạ và output ngoài schema.

Schema chưa đủ semantic state machine: confidence là một điểm chung, không calibration; constraint chưa mang source span/provenance/confidence riêng; conversation reference là một chuỗi; nhiều product mentions và ADD/REPLACE/REMOVE/KEEP chưa được mô hình hóa đầy đủ. Cần mở rộng sau khi có nhãn hội thoại độc lập.

## 8. Rule-based và semantic-based

| Nhiệm vụ | Hiện tại | LLM |
| --- | --- | --- |
| Giá, quantity, clear category/identity, xếp giá | Deterministic parser + taxonomy + repository | Không cần ở fast path |
| Tìm tên/không dấu/typo nhẹ | Normalize và catalog candidate matching | Không trong benchmark parser/tool |
| Công dụng không có field xác minh | Nêu thiếu dữ liệu/hỏi rõ | Không được để LLM tự khẳng định |
| Endpoint hội thoại | Agent/tool flow hiện hành | Có thể gọi Groq |
| Structured extraction | Strict schema + expected-field validation, 60 case | Ablation; 20/60 pass, 34 provider errors, 6 response sai/không đủ chuẩn, chưa promote |

Schema hợp lệ không đồng nghĩa intent đúng; LLM không phải nguồn sự thật cho giá/SKU/tồn.

## 9. Retrieval/ranking và ablation

Pipeline: exact identity → candidate generation từ taxonomy/catalog → lọc category/brand/giá/phủ định có trường → xếp theo tiêu chí tính được → grounded response. Price order dùng price_vnd. Không suy sales/rating từ tồn kho hoặc thứ tự output.

Ablation retrieval lịch sử trên câu tên hàng, không phải semantic-needs benchmark:

| Dataset | BM25 R@1/R@5 | multilingual E5-small R@1/R@5 | RRF R@1/R@5 |
| --- | ---: | ---: | ---: |
| Dev, 150 query tên hàng | 0,9800 / 1,0000 | 0,9667 / 0,9800 | 0,9733 / 0,9933 |
| SKU held-out, 360 query tên hàng | 0,9889 / 1,0000 | 0,9389 / 0,9833 | 0,9722 / 0,9917 |

BM25 thắng trên title queries; không thể kết luận BM25 tốt hơn cho mô tả nhu cầu. Vì vậy giữ lexical/exact, chưa tích hợp dense. Artifact: [retrieval dev](artifacts/generalized_retrieval_variants_dev_20261027.json), [retrieval held-out](artifacts/generalized_retrieval_variants_heldout_20261027.json).

## 10. Phương pháp benchmark

Dataset: evaluation/cases/retail_ai_semantic_intelligence_v2_frozen_20261028.json
Dataset SHA-256: d030d8f860f426a0ffcba7d44b542f8f7b23e9d361a51d8810c052670ba8b1e9
Catalog SHA-256: a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6
300 query: 100 product resolution, 100 category browse, 100 hard constraint; 50 conversations/200 turns. Builder generates phrases from catalog name, taxonomy, price, brand and fixed seed. Gold identity/category come from source rows, not model output.

This is a synthetic development/regression set and was used to inspect/fix failures. Do not call it independent human holdout. Product labels mostly select one SKU; alternatives, accepted substitutes, query style/user variation and semantic product suitability are underrepresented.

Metrics: Recall@1/@5/MRR for gold ID; Precision@5/category parser accuracy; hard constraint violations and correct empty results; context/category retention and price ordering; parser-only intent/reference. No human quality grade.

## 11. Gold-label/grader audit

- SKU/category/price/brand labels come from snapshot and generated expected fields.
- Category Precision@5 checks taxonomy membership, not whether a product meets the user’s purpose.
- Hard constraints compare catalog price/category/brand, not human suitability.
- Grader initially counted any “khác” as alternative. It falsely marked “Các loại bột khác”. Fixed by removing the expected taxonomy phrase from the query before detecting alternative request; expected category—not parser output—is used to avoid hiding parser mistakes.
- 5 regression cases now pass: taxonomy “khác”, real alternative phrase, “khác nhau” comparison, category phrase with actual alternative and English request.
- Alternative metric checks parser intent only; it does not assert a new SKU was returned. Gold labels also need human review for duplicates/acceptable substitutes.

## 12. Baseline vs final

| Metric | Baseline | Final |
| --- | ---: | ---: |
| Product Recall@1 / @5 / MRR, n=100 | 0,32 / 0,37 / 0,3461 | 1,00 / 1,00 / 1,00 |
| Category Precision@5, n=100 | 0,80 | 1,00 |
| Category parser accuracy / errors | 0,95 / 0 | 1,00 / 0 |
| Hard-constraint case pass, n=100 | 0,57 | 1,00 |
| Constraint violations | 1 | 0 |
| Candidate Recall@5 / nonempty | 0,7628 / 0,58 | 0,98 / 0,98 |
| Conversation scope/context/parser scope | Chưa có đối chứng cùng metric | 100/100 |
| Price ordering | Chưa đo | 50/50 |
| Alternative intent sau sửa grader | Chưa đo | 50/50 |
| First-item parser reference | Chưa đo | 50/50 parser-only |

Two hard-constraint empty results are correct abstentions because the snapshot has no eligible catalog row for those category/budget/excluded-brand combinations. Full row-level JSON: [baseline](artifacts/retail_ai_semantic_intelligence_v2_baseline_20261028.json), [LLM ablation](artifacts/retail_ai_semantic_intelligence_v2_baseline_with_llm_20261028.json), [final](artifacts/retail_ai_semantic_intelligence_v2_final_20261028.json).

## 13. Kết quả theo nhóm và ca cải thiện

- Nhận diện tên/variant SKU trong 100 query generated: gold @1/@5 100%.
- Giữ taxonomy cụ thể khi từ như “hướng/mùi” nằm ngay trong nhãn category; tách phần mở đầu “mình đang cần” khỏi product type.
- Giữ scope 100/100 lượt follow-up parser+tool; sắp giá tăng dần 50/50.
- Câu “giá loại đầu tiên” được API stateful kiểm tra trong cùng session: ID và giá giống sản phẩm đã trả.
- /agent/respond trả card Coca-Cola Zero có ID và giá đối chiếu snapshot đúng.
- Smoke /agent/respond/stream: 16 kịch bản/17 turns, HTTP errors 0, invariant failures 0, complete event 100%, identity/price match 100%.
- Không coi các điểm trên là chấm prose/naturalness. Smoke chạy bằng FastAPI TestClient trong process.

Artifact: [live hard-case smoke](artifacts/generalized_live_hard_cases_20261028.json).

## 14. Ca còn lỗi/chưa chứng minh

1. Không đủ thuộc tính để chọn công dụng chính xác: ví dụ “thịt thăn bò ăn lẩu”, dầu gội cho da đầu nhanh bết; không được đoán từ tên category.
2. Không có sales count/rating nên “bán chạy/ngon nhất” không thể khẳng định.
3. Hai case constraint đúng khi trả rỗng, nhưng tồn kho snapshot không đại diện tồn hiện tại.
4. Hội thoại alternative chưa xác minh SKU mới end-to-end cho cả 50 session.
5. Structured LLM extraction không đạt độ tin cậy để dùng: 20/60 pass, 34 provider errors, 6 lỗi output/extraction.
6. Chưa có user/annotator-created test set về phương ngữ, dấu, ASR noise, câu dài, phủ định phức, nhiều ý định, đổi ý.
7. Chưa human-grade naturalness, usefulness, clarification quality, hallucination/unsupported-claim rate.
8. Không chạy giỏ hàng/checkout thật, không giao dịch thật, không test microphone/STT/TTS trong V2.

## 15. Độ trễ và tài nguyên

| Local parser + catalog tool, 300 queries | Baseline | Final |
| --- | ---: | ---: |
| p50 | 131,084 ms | 275,125 ms |
| p95 | 484,533 ms | 430,946 ms |
| Mean | 181,942 ms | 288,025 ms |

Trong lần này p50/mean tăng; p95 giảm khoảng 11%. Không tuyên bố hệ thống nhanh hơn tổng thể. Final parser riêng p50/p95 60,504/99,278 ms; catalog tool p50/p95 207,058/346,811 ms. In-process local, nhạy với cache/CPU.

LLM ablation p50/p95 1,930/2,663 ms trên các response có thời gian; nhiều provider error nên không đại diện toàn request. SSE smoke p50/p95/mean 448/1.202/558 ms trong TestClient, không gồm network/browser. SSE gửi deltas sau khi tool grounding xong, chưa phải đo provider token streaming. Peak RAM, CPU, throughput/concurrency, token/cost không được đo.

Lịch sử E5: cold index build 67–97s, warm query 8–10ms, RAM chưa đo. Không dùng model trong production.

## 16. Ablation và quyết định

| Phương án | Kết quả | Quyết định |
| --- | --- | --- |
| Raw query baseline | Product R@1 .32; category P@5 .80; constraint pass .57 | Giữ đối chứng |
| Parser + catalog tool | Product R@1/@5 1.00; category P@5 1.00; zero violations | Giữ fast path |
| LLM structured parse | 20/60 pass, 34 provider errors, latency cao | Không promote |
| BM25 | Thắng title query set | Giữ lexical |
| E5 / RRF | Không vượt BM25 trên title queries; semantic query set chưa có | Không promote |
| Cross-encoder/BGE/Qwen embedding | Chưa có nhãn/tài nguyên để chứng minh | Chưa triển khai |

## 17. Regression và xác minh

- Full suite: uv run pytest tests -q --tb=short — 1.203 passed trong 704,98s; chạy sau thay đổi production, trước thay đổi eval grader.
- Summary của lần full suite được lưu tại [pytest full-suite log](artifacts/pytest_full_20261028_v2.log).
- Sau chỉnh grader: 5 focused tests passed.
- Ruff: uv run ruff check backend tests evaluation — All checks passed sau thay đổi grader.
- Final benchmark chạy lại và lưu artifact JSON sau khi sửa grader.
- Git diff --check sẽ được chạy sau khi ghi file báo cáo.
- Vì full suite chạy trước eval-only change, chạy lại full suite trước merge nếu eval code đổi thêm. Không thay đổi production sau full-suite run.

## 18. Giới hạn dữ liệu

- Catalog có 3.099 raw rows, 3.081 sản phẩm hợp lệ, 18 dòng cách ly; 346 taxonomy paths/344 unique leaves.
- 2.868 mô tả lặp tên mặt hàng; 3.092 mô tả ngắn dưới 80 ký tự.
- Snapshot 15/09/2026; giá/tồn không phải real time.
- Thiếu sales/rating, thành phần, công dụng và thuộc tính phù hợp cho nhiều SKU.
- Benchmark V2 template hóa câu và chưa bao phủ độc lập đủ thương hiệu/quy cách/taxonomy.

## 19. Rủi ro

| Rủi ro | Hướng giảm |
| --- | --- |
| Catalog cũ/thiếu thuộc tính | Gắn ngày snapshot, không cam kết tồn hoặc suitability không có evidence. |
| Parser không hiểu cách nói mới | Thu thập labeled user utterance; sửa theo root cause, không thêm rule theo SKU. |
| LLM provider unstable | Chưa promote extraction; strict validation và local fallback. |
| Điểm synthetic lạc quan | Blind human review, tách dataset độc lập theo người viết/cách diễn đạt. |
| Alternatives lặp SKU | Bổ sung seen/selected IDs và assert candidate mới qua API. |
| Chi phí/tải chưa rõ | Đo concurrency, throughput, peak RAM/CPU và provider/token cost trước release. |

## 20. Việc tiếp theo

1. Viết/annotate độc lập ít nhất 300 câu thực tế, 100 hard-negative/multi-constraint, 50 hội thoại; dùng nhãn Exact/Substitute/Complement/Irrelevant và rubric suitability.
2. Split test theo user/phrase/intent, blind run và không sửa code theo held-out.
3. Enrich catalog bằng nguồn/provenance/date/verified status cho công dụng, thành phần, đơn vị và stock.
4. Thêm end-to-end alternative test có seen SKU IDs; test multi-entity compare, intent correction và đổi nhóm.
5. Mở rộng state thành ADD/REPLACE/REMOVE/KEEP; thêm source span/confidence từng constraint.
6. Chỉ đánh giá LLM lại trên tập độc lập, paired accuracy, latency, provider reliability và cost/token.
7. Thử dense/hybrid/reranker trên relevance labels thật; chỉ promote khi quality/latency/resources tốt hơn.
8. Human review tối thiểu 50 response blind về tự nhiên, hữu ích, grounded, clarification và hallucination.
9. E2E browser/network, concurrency/load, peak RAM/CPU, SSE first-byte/full completion.
10. Đánh giá STT riêng bằng audio/transcript thật trước khi nói về độ chính xác voice.

## Tái lập và artifacts

Chạy từ root:

    uv run python evaluation/semantic_intelligence_v2_benchmark.py --output docs/reports/artifacts/retail_ai_semantic_intelligence_v2_final_20261028.json
    uv run python evaluation/semantic_intelligence_v2_benchmark.py --with-llm --llm-sample-size 60 --output docs/reports/artifacts/retail_ai_semantic_intelligence_v2_baseline_with_llm_20261028.json
    uv run python evaluation/generalized_live_consultation_smoke.py
    uv run pytest tests -q --tb=short
    uv run ruff check backend tests evaluation

- [V2 baseline](artifacts/retail_ai_semantic_intelligence_v2_baseline_20261028.json)
- [LLM extraction ablation](artifacts/retail_ai_semantic_intelligence_v2_baseline_with_llm_20261028.json)
- [V2 final after grader correction](artifacts/retail_ai_semantic_intelligence_v2_final_20261028.json)
- [Live API/SSE smoke](artifacts/generalized_live_hard_cases_20261028.json)
- [Full-suite test summary](artifacts/pytest_full_20261028_v2.log)
- [Frozen provisional dataset](../../evaluation/cases/retail_ai_semantic_intelligence_v2_frozen_20261028.json)
- [Benchmark source](../../evaluation/semantic_intelligence_v2_benchmark.py)

LLM ablation gọi provider và có thể tiêu quota/biến động theo availability; artifact hiện tại là kết quả của lần chạy đã ghi, không chạy lại chỉ để lấy số đẹp hơn.

## Addendum — kiểm toán sau benchmark, 28/10/2026

Đối chiếu 50 cuộc hội thoại với output lưu trong artifact cho thấy benchmark V2 chỉ chấm parser intent cho câu hỏi “loại khác”, không chấm tính mới của SKU trả về: 48/50 lượt có SKU trùng danh sách trước; 42/50 trả lại đúng cả 5 SKU. Vì vậy `50/50 alternative intent` ở trên chỉ là tỷ lệ parser nhận ra ý định, tuyệt đối không phải tỷ lệ tư vấn alternative thành công. API thật cũng tái hiện lỗi ở lượt alternative thứ ba do session chỉ nhớ trang gần nhất và preference text tích lũy câu “khác” thành constraint rác.

Đã thêm `seen_product_ids` theo scope vào session, dọn phần câu chữ pagination khỏi preferences, tăng giới hạn loại trừ nội bộ, trả lời rõ khi hết lựa chọn mới, và thêm kiểm thử nhiều trang dầu gội/nước giặt, chuyển nhóm, hết lựa chọn, không tự thêm giỏ và cô lập tham chiếu ordinal giữa session. Các kiểm thử này chứng minh regression path cụ thể; chúng không biến bộ câu sinh thành human evaluation.

Audit dataset cũng phát hiện H-037/H-082 dùng từ “dưới” trong khi generator chọn ngưỡng chỉ có sản phẩm bằng giá. Dataset dev sửa lại dùng ngưỡng “tối đa”, thêm SHA-256 catalog và bỏ timestamp biến đổi; xây lại hai lần cho cùng SHA-256 `8f129beecee14b7c8ff1bbba106fae154a3321a55ef2efaad718e3493de27d3b`. Dataset mới và benchmark mới được lưu riêng, không sửa artifact lịch sử. Kết quả vẫn provisional/synthetic; xem report production-quality master để biết đầy đủ giới hạn.

## Addendum — preference follow-up và xác minh cuối, 28/10/2026

- Bổ sung guard tổng quát cho tool `plan_shopping_mission`: nếu câu hiện tại có dấu hiệu sở thích rõ và parser dùng chung trả được soft preference, backend đưa preference vào state kể cả khi model bỏ sót; không đổi nó thành hard requirement. Ngân sách/party size vẫn lấy theo state và preference đổi ngân sách không bị ghi nhầm thành sở thích.
- Thêm kiểm thử model bỏ sót preference, ngân sách chỉ đổi, và phần trả lời xác nhận preference mà không tuyên bố catalog đã xác minh độ phù hợp.
- Sau thay đổi: `uv run pytest tests -q --tb=short` — **1.222 passed trong 241,30 giây**; `uv run ruff check backend tests evaluation` — pass; `git diff --check` sạch.
- Live Groq probe sau patch: **5/5** HTTP 200, **5/5** kiểm tra grounding tự động pass, 2 lượt gọi Qwen, không fallback. Lượt follow-up có câu xác nhận preference. Raw trace cho thấy ở lượt chạy này Qwen tự đưa preference vào tool arguments, nên unit test mới là bằng chứng trực tiếp cho trường hợp backend phải khôi phục field bị model bỏ sót.
- Danh sách picnic vẫn chưa có snack “không cay”: catalog không có thuộc tính xác minh độ cay; hệ thống giữ abstention, không bịa hoặc tính món thay thế vào tổng.
- Đánh giá vẫn **demo-only / chưa sẵn sàng production** vì thiếu qrels và review con người độc lập, chưa benchmark công suất triển khai thật, và snapshot không có nhiều thuộc tính sản phẩm. Báo cáo đầy đủ: [`retail_ai_production_quality_master_report.md`](retail_ai_production_quality_master_report.md).

## Addendum — regression sweep tổng quát, 29/10/2026

Đợt này xử lý các lỗi xuất hiện trên nhiều nhóm hàng và nhiều cách diễn đạt, không thêm rule theo SKU:

- Tách query sản phẩm đã rút gọn khỏi câu nói đầy đủ: giữ nguyên ngữ cảnh câu gốc để nhận diện tên hàng/biến thể, nhưng tuyến tra cứu fact không dùng lại câu giá/tồn kho làm từ khóa tìm kiếm. Khi người dùng sửa câu bằng “ý mình là…”, phần đính chính không bị đọc thành thuộc tính phủ định.
- Giữ số đo sản phẩm như `500 ml`, `1 lít`, `2,9 kg`; không chuyển số cân nặng/khối lượng thành ngân sách.
- Không coi cụm danh mục chưa đầy đủ như “nước lau” là một lá taxonomy cứng. Tên danh mục chỉ được dùng làm bộ lọc cứng khi trùng một lá thật trong snapshot; từ khóa danh mục chỉ làm tín hiệu xếp hạng nếu có tên hàng hỗ trợ.
- Với truy vấn biến thể theo thương hiệu nhưng chưa nêu quy cách cụ thể, trả các biến thể trong đúng nhóm để khách tự chọn thay vì chốt SKU đầu tiên; kiểm thử gói Huggies size L trả cả L38 và L68.
- Tách “bỉm” khỏi “bim bim”, nhận diện lời nhắc bé kèm cân nặng là đối tượng trẻ em, lọc nhóm hàng phù hợp theo dữ liệu audience; đồng thời nói rõ catalog không xác minh dải cân nặng/kích cỡ trên bao bì.
- Mở rộng cách nhận “nước uống” sang nhánh đồ uống trong taxonomy để truy vấn “không đường” chỉ trả món có bằng chứng trong tên hàng như “Zero”; nếu thuộc tính không có dữ liệu thì vẫn từ chối xác nhận.
- Khôi phục sở thích mềm từ câu tự nhiên khi tool call bỏ sót, nhưng không nâng sở thích thành điều kiện bắt buộc. Ví dụ “ưu tiên món dễ chia cho mọi người” vẫn là gợi ý mềm.

### Kết quả kiểm tra

- Nhóm regression tập trung: **29 passed**.
- Full suite hiện tại: `uv run pytest tests -q --tb=short` — **1.337 passed trong 176,22 giây**.
- Ruff backend/tests/evaluation, `compileall` và `git diff --check`: pass sau khi xác minh cuối.
- Toàn bộ thay đổi nằm trên nhánh `codex/retail-text-quality-v4-20261019`; không tạo nhánh mới.

Đây là bằng chứng regression trên test suite của repo, không phải human evaluation. Điểm benchmark V2 trong các phần trên vẫn là synthetic/provisional; chưa có blind holdout, qrels độc lập, human grading hay thuộc tính catalog đủ để kết luận độ phù hợp sản phẩm. Trạng thái phát hành vẫn **demo-only / chưa sẵn sàng production**.

### Benchmark xác minh lại

- Chạy lại bộ frozen 300 câu / 50 hội thoại với catalog snapshot cùng SHA-256 `d030d8f860f426a0ffcba7d44b542f8f7b23e9d361a51d8810c052670ba8b1e9`.
- Kết quả khớp artifact đã dùng grader sửa: product R@1/R@5 **1,00/1,00**; category P@5 **1,00**; hard-constraint pass **100/100**, **0** vi phạm; candidate Recall@5 **0,6878 trên 98 case có gold candidate**, nonempty **0,98**. Không có thay đổi output ở 100 case hard-constraint so với lần corrected run gần nhất.
- Giá trị **0,98** trong artifact HEAD cũ là từ grader sai; báo cáo production-quality master đã ghi rõ cách sửa denominator/rank window và thay thế số đó. Không xem chênh lệch này là hồi quy của patch hiện tại.
- Artifact chạy lại: [`retail_ai_semantic_intelligence_v2_post_regression_20261029.json`](artifacts/retail_ai_semantic_intelligence_v2_post_regression_20261029.json). Latency deterministic lần này p50/p95 **85,778/140,196 ms**; chỉ là benchmark cục bộ, không đại diện độ trễ LLM hay production.
