# Báo cáo kỹ thuật, nghiên cứu và đánh giá Retail AI

- **Ngày đánh giá:** 19/10/2026 (Asia/Saigon)
- **Nhánh follow-up:** `codex/session-isolation-ui-hardening`
- **Commit gốc trước follow-up:** `ce8d3b51e73110ee437a734b3ff98f95f447bb13`
- **Mốc của báo cáo đánh giá trước:** `1757bbd988de141ae23a6cf3f475a9c496d0340f`
- **Baseline:** 2f8b0368cf006286f3188d0f2188ee728fac954d
- **Môi trường giao dịch:** catalog demo và checkout sandbox; không có giao dịch tiền thật
- **Báo cáo chăm sóc khách hàng cũ:** được giữ tại [archive/retail-ai-optimization-report-customer-care-2026-10-19.md](archive/retail-ai-optimization-report-customer-care-2026-10-19.md)

## 1. Executive Summary

Retail AI đã có thể dẫn dắt phiên mua sắm demo từ tra cứu sản phẩm, tư vấn có ràng buộc, cập nhật ngữ cảnh, thêm nhiều món vào giỏ, checkout có xác nhận, đến kiểm tra trạng thái thanh toán sandbox hoặc mở yêu cầu hỗ trợ demo. Backend kiểm soát thao tác nhạy cảm; model không tự sửa giá/tồn hay chuyển đơn thành PAID.

Benchmark tiếng Việt phát hiện ba vấn đề: grader tìm tham số recommendation trong trace LLM dù router chạy xác định; câu “chuyển khoản xong rồi” không được nhận diện khi chờ thanh toán; yêu cầu “bỏ qua xác minh” có thể bị hiểu thành gọi nhân viên. Sau sửa, hội thoại live v2 đạt 13/13 assertion; ba ca mặc định về thêm giỏ, checkout giỏ trống và yêu cầu nhân viên demo đạt 3/3.

Trong báo cáo đánh giá trước, commit `1757bbd` đạt **490 backend**, **41 frontend**, **40/40 P0 regression** và **20/20 shopping sandbox**. Follow-up trên nhánh nêu trên hiện đạt **514 backend**, **49 frontend**, **40/40 P0 regression**, **20/20 shopping sandbox** và smoke backend **27 PASS / 0 FAIL / 3 UNSUPPORTED / 1 NOT RUN**; TypeScript, production build, Ruff, compileall và diff check đều qua. Đây là bằng chứng test/sandbox và một mẫu Groq live nhỏ, không phải độ chính xác khách hàng thật hay chứng nhận production.

Giới hạn chính: probe PostgreSQL `SELECT 1` timeout sau khoảng 2 giây và Redis `PING` không kết nối được; demo có 6 sản phẩm; kho WinMart 3.099 dòng là snapshot, không phải tồn kho chi nhánh thời gian thực; ticket nhân viên và payment chỉ là simulator; chat gửi SSE delta sau khi model và backend hoàn tất kiểm tra, chưa stream token trong lúc model đang sinh; voice/STT/TTS và kiosk thật không nằm trong benchmark này.

## Follow-up Validation: Session Isolation and Product Disambiguation

Các thay đổi follow-up trên nhánh `codex/session-isolation-ui-hardening` tập trung vào riêng tư phiên, phân biệt sản phẩm và phản hồi dần trong kiosk:

- Kiosk gắn request đang chờ với generation của phiên; câu trả lời đến muộn sau khi khách A kết thúc không được đưa vào hội thoại khách B. Audio đã thu không bị gửi lại sang phiên mới khi phiên cũ hết hạn. Khởi tạo phiên được single-flight và component đóng phiên đang dùng khi bị tháo.
- Khi đóng phiên, cart của khách bị xóa; order đang `PENDING` được giữ trong kho order tách biệt và chỉ đọc được bằng session sở hữu nó.
- Truy vấn tiếng lóng `bim bim`/`bimbim` chỉ match cụm trong tên sản phẩm để tránh va chạm khi bỏ dấu với danh mục tã/bỉm. Truy vấn có dấu `bỉm` thêm điều kiện danh mục tã/bỉm.
- So sánh hai sản phẩm chỉ chạy khi khách nêu rõ cặp món hoặc có lượt so sánh trước; giá/tồn được đọc lại từ catalog và câu trả lời nêu nguồn dữ liệu. Các câu hỏi “món nào ngon/bán chạy nhất” tiếp tục qua luồng tư vấn, không bị nhầm thành so sánh.
- `evaluation/cases/product_resolution_v2.jsonl` có 23 truy vấn, gồm snack, tã/bỉm, đồ uống, hóa mỹ phẩm, thực phẩm và sữa chua. Test xác nhận mọi gold product ID có trong 3.081 sản phẩm được importer chấp nhận từ snapshot 3.099 dòng. Đây là kiểm tra dữ liệu và SQL dựng ra, chưa phải đo Recall trên PostgreSQL.
- Chat kiosk dùng SSE để cập nhật câu trả lời theo từng cụm từ. Endpoint chạy xong policy, tools, grounding và xử lý giao dịch trước khi phát delta; việc này làm nội dung hiển thị dần nhưng không giảm thời gian chờ model tạo câu trả lời.
- Khi yêu cầu gặp lỗi mạng, nút thử lại dùng lại `client_turn_id` của lượt đó và không thêm dòng câu hỏi trùng trong UI.

| Kiểm tra follow-up | Kết quả | Giới hạn |
|---|---:|---|
| Backend pytest | **514 passed** | Catalog demo/in-process; không bao gồm DB production. |
| Frontend Vitest | **49 passed / 6 files** | Component/JSDOM; không phải browser E2E. JSDOM báo các API media/canvas chưa giả lập. |
| Vue TypeScript + production build | **Pass** | Main JS 171,31 kB (gzip 56,23 kB); avatar/model-viewer ở chunk tải riêng 1.024,05 kB (gzip 290,30 kB), Vite vẫn cảnh báo chunk lớn. |
| Ruff, compileall, `git diff --check` | **Pass** | Có cảnh báo chuẩn hóa LF/CRLF của Git trên Windows. |
| PostgreSQL product search v2 | **Không đo được** | Chạy read-only benchmark kết thúc bằng `TimeoutError`; không có Recall/latency mới để báo cáo. |
| P0 regression | **40/40 pass** (8 nhóm × 5 lượt) | Scripted responses; artifact tạo trên commit `d64b751`: `evaluation/results/p0_regression_20261019T100745Z.json`. |
| Shopping sandbox | **20/20 pass**, 98 API calls, 0 LLM calls | False-payment 0/4; cross-session leakage 0/4; in-process P50 0,937 ms, P95 9,597 ms. Artifact `evaluation/results/shopping_flow_sandbox_20261019T100744Z.json` ghi commit `d64b751`. |
| Deterministic smoke benchmark | **27 PASS / 0 FAIL / 3 UNSUPPORTED / 1 NOT RUN** | Bao gồm hội thoại giỏ, tham chiếu món trước, payment sandbox và so sánh hai sản phẩm cùng lượt hỏi “cái nào rẻ hơn”. Scripted Groq/demo catalog; không đo live tool planning. Artifact tạo trên commit `d64b751`: `evaluation/results/smoke_benchmark_coverage_20261019_postpush_d64b751.json`. Ba mục chưa hỗ trợ là FEFO theo lô, knowledge base sản phẩm và RAG; Redis live tách thành NOT RUN. Các artifact trước được giữ nguyên để bảo toàn lịch sử. |
| Live product consultation (18/10) | **6/6 HTTP 200; 6/6 grounding/safety assertions**; median 12.473 ms; 7 LLM calls, 6 tool calls | Qwen `qwen/qwen3.8-27b`, fallback bật; PostgreSQL catalog 3.099 rows, 4 tool turns được gọi. Một lượt mỗi case, không chấm naturalness bởi người. Artifact `evaluation/results/live_vietnamese_product_consultation_final_20261018.json`; là kết quả lịch sử, không phải probe DB hiện tại. |
| External data stores | PostgreSQL **NOT RUN** (read-only `SELECT 1` timeout, ~2,01 s); Redis **NOT RUN** (`PING` connection error, ~2,03 s) | Không ghi các probe là PASS; không khởi động local services. |

Các kiểm thử phiên bao gồm late response sau đổi khách, không replay audio, single-flight boot, đóng phiên khi unmount và luồng UI từ sản phẩm đến thanh toán mô phỏng. Kiểm thử SSE bao gồm byte UTF-8 chia nhỏ, ráp delta và từ chối stream bị cắt trước event hoàn tất. Chúng chưa thay thế kiểm thử trình duyệt, micro thật hoặc backend đang kết nối PostgreSQL/Redis.

## 2. Repository Audit

### Kiến trúc thực tế

| Thành phần | Trách nhiệm và trạng thái |
|---|---|
| backend/api/main.py | FastAPI, policy, intent routing, grounding và điều phối; tệp còn khoảng 4,9 nghìn dòng, một số trách nhiệm vẫn tập trung tại đây. |
| backend/services/retail_agent.py | Vòng gọi tool LLM; tối đa 2 vòng, 5 tool calls và một retry timeout theo cấu hình. Có unit/API coverage; chưa kiểm tra live tool planning ở mọi lượt. |
| backend/services/retail_tools.py | Hợp đồng và thực thi tìm kiếm, snapshot, recommendation, promotion/store info và các thao tác đọc. |
| backend/services/vietnamese_intent.py | Chuẩn hóa dấu, cách viết chat và một số biến thể vùng miền; deterministic routing cho intent phổ biến, chưa bao phủ hết cách nói tự do. |
| backend/services/sessions.py | ShoppingSessionState, TTL, state version, SET/REPLACE/REMOVE/KEEP. Redis adapter có fail-closed; live Redis hiện không kết nối được. |
| backend/services/shopping_flow.py và backend/services/shopping.py | Phân loại cart/checkout; cart, quote, order, idempotency và payment sandbox. Dữ liệu ghi là in-memory demo, không phải POS/order DB bền. |
| backend/services/support.py | Phân loại vấn đề và ticket idempotent trong SupportTicketStore một process; trạng thái RECEIVED_DEMO, không thông báo nhân viên thật. |
| backend/products/postgres_repository.py | Đọc catalog PostgreSQL; không coi catalog snapshot là tồn chi nhánh. Query live không kiểm chứng do timeout. |
| apps/kiosk-airi/src/App.vue | Kiosk Vue: chat, session, product cards, cart/checkout và hỗ trợ. Unit test/build pass; chưa có e2e trên browser kiosk thật. |
| data/ và backend/data/ | Snapshot sản phẩm, nguồn doanh nghiệp, planogram và schema/import. Ảnh 3D là sơ đồ đề xuất từ dữ liệu gán kệ, không phải bản vẽ hiện trạng. |

### Mức hỗ trợ dựa trên bằng chứng

| Mức | Chức năng |
|---|---|
| Implemented and verified | Tra cứu sản phẩm demo; trả giá/kệ/tồn khi field có dữ liệu; từ chối thuộc tính thiếu nguồn; recommendation theo nhóm/giá/cay và constraint đa lượt; giỏ nhiều món; sửa/xóa số lượng; checkout token/xác nhận; false-payment guard; idempotency; session ownership; ticket demo; kết thúc phiên; prompt-injection guard; tiếng Việt và một phần tiếng Anh. |
| Implemented but not verified live | Catalog Postgres chỉ đọc; Redis sessions; một phần runtime/schema/tool cho campaign, hàng mới, store profile, receipt, nearby POI và nguồn doanh nghiệp. Feed/DB không hoạt động trong probe nên chưa xác minh dữ liệu chi nhánh thật. |
| Partially implemented | Agent/policy orchestration còn tập trung trong main.py; state extraction dựa nhiều vào cụm/regex; tiếng Anh có test nhưng live v2 chỉ tiếng Việt; UI chưa browser-tested; chất lượng hội thoại tự nhiên cần người chấm. |
| Documented only hoặc thiếu runtime | Tài liệu requirements/architecture/tool contracts mô tả một số gateway/module riêng chưa có class/runtime tương ứng. RAG tài liệu đã duyệt chưa thành retrieval pipeline độc lập. |
| Missing | POS/tồn chi nhánh live; thanh toán thật; CRM/handoff thật; số liệu bán chạy/hài lòng; tải production; đánh giá tiếng nói và streaming end-to-end trong lượt này. |

**Ngoài phạm vi theo yêu cầu trước:** hỏi địa điểm chung không gắn cửa hàng cụ thể bị từ chối do không có chi nhánh/tọa độ đã xác minh. Nearby POI cho một cửa hàng chỉ có thể trả lời khi feed và attribution sẵn sàng.

## 3. Research & Literature Review

Nghiên cứu dưới đây định hình test và lựa chọn kiến trúc; kết quả của paper không phải điểm số Retail AI.

| Nghiên cứu | Bài toán, phương pháp và kết quả công bố | Áp dụng và giới hạn |
|---|---|---|
| **ProductAgent: Benchmarking Conversational Product Search Agent with Asking Clarification Questions** — Jingheng Ye, Yong Jiang, Xiaobin Wang, Yinghui Li, Yangning Li, Hai-Tao Zheng, Pengjun Xie, Fei Huang (2024). [arXiv:2407.00942](https://arxiv.org/abs/2407.00942) | PROCLARE đánh giá hỏi làm rõ, tóm tắt thuộc tính, query generation và retrieval động. Ở thiết lập truy vấn truyền thống của Table 4, GTE đạt MRR@10 52,29; rerank GTE 63,82. Hiệu quả phụ thuộc dữ liệu và lượt hội thoại. | Ưu tiên lexical/fuzzy baseline và hỏi khi thiếu thuộc tính ảnh hưởng quyết định. Kết quả không chứng minh vector DB tốt hơn trên WinMart; chưa thêm vector DB khi benchmark dự án chưa cho thấy lợi ích. |
| **ShoppingBench: A Real-World Intent-Grounded Shopping Benchmark for LLM-based Agents** — Jiangyuan Wang et al. (2025). [arXiv:2508.04266](https://arxiv.org/abs/2508.04266) | 3.310 chỉ dẫn mua sắm, sandbox hơn 2,5 triệu sản phẩm; GPT‑4.1 đạt absolute success rate 48,2% tổng thể trong thiết lập tác giả. | Dùng để nhấn mạnh chuỗi hành động, budget, coupon và nhiều sản phẩm. Không dùng số paper làm kết quả Retail AI. |
| **Shopping Reasoning Bench: An Expert-Authored Benchmark for Multi-Turn Conversational Shopping Assistants** — Shuxian Fan, Seonwoo Min, Youna Hu, Botao Xia, Jayakrishnan Unnikrishnan, Rowan Musselmann, Yifan Gao, Qingyu Yin, Priyanka Nigam, Bing Yin (2026). [arXiv:2606.12608](https://arxiv.org/abs/2606.12608) | 525 mission (232 single-turn, 293 multi-turn), 10.863 rubric nhị phân có trọng số. Chín model đạt 57–77% overall; ở multi-turn, rubric tùy chọn thấp hơn rubric bắt buộc 13–29 điểm. Chất lượng lượt cuối giảm 4–18 điểm so với lượt đầu tùy model. | Tách rubric yêu cầu bắt buộc khỏi tư vấn thêm; thêm ca sửa constraint, tham chiếu cũ, cart và payment. Bộ v2 có 7 hội thoại, nhỏ hơn nhiều và không phải expert annotation. |
| **RealWorldShop: Benchmarking and Improving Conversational Shopping Agents in Real-World E-commerce** — Xinwei Yang et al. (2026, arXiv preprint). [arXiv:2609.38974](https://arxiv.org/abs/2609.38974) | Bản v1 ngày 30/09/2026 dùng 3,28 triệu sản phẩm, khoảng 1.200 shopping scenarios và hơn 2.000 profile tổng hợp. Full RealShop_Agent đạt success 0,82; prompt-only Qwen3.5-27B Base đạt 0,55, còn hệ có session control trên backbone gốc đạt 0,71. | Củng cố session state, flow control, retrieval có grounding và runtime guards; áp dụng bằng kiểm thử cách ly phiên và catalog. Đây là preprint và kết quả simulator của tác giả, không phải bằng chứng hiệu năng hoặc độ chính xác của Retail AI. |
| **Berkeley Function-Calling Leaderboard (BFCL)** — Berkeley Gorilla Team (V3/V4, 2025). [Leaderboard](https://gorilla.cs.berkeley.edu/leaderboard) · [V4 methodology](https://gorilla.cs.berkeley.edu/blogs/15_bfcl_v4_web_search.html) | BFCL V3 bổ sung multi-turn/multi-step calls; V4 agentic bổ sung web search, memory và format sensitivity, với kiểm tra AST/state transition. | Grader kiểm tra state/cart/order/ticket thay vì chỉ câu chữ. Không chạy BFCL đầy đủ; số pass nội bộ không phải BFCL score. |
| **ViWOZ: A Multi-Domain Task-Oriented Dialogue Systems Dataset For Low-resource Language** — Phi Nguyen Van et al. (2022). [arXiv:2203.07742](https://arxiv.org/abs/2203.07742) | Dataset tiếng Việt gồm 5.000 dialogues và 60.946 annotated utterances. | Gợi ý biến thể tiếng Việt và multi-turn; không phải miền retail nên không suy ra retail accuracy. |
| **Diable: Efficient Dialogue State Tracking as Operations on Tables** — Pietro Lesci, Yoshinari Fujinuma, Momchil Hardalov, Chao Shang, Yassine Benajiba, Lluis Marquez (Findings ACL 2023). [ACL Anthology](https://aclanthology.org/2023.findings-acl.615/) · DOI 10.18653/v1/2023.findings-acl.615 | Biểu diễn state thành bảng và áp dụng thao tác theo lượt. Trên MultiWOZ, paper báo cáo nhanh hơn 2,4× phương pháp được so sánh, với Joint Goal Accuracy cạnh tranh. | Củng cố SET/REPLACE/REMOVE/KEEP. Đây không phải số latency/accuracy của Retail AI. |
| **Wizard of Shopping: Target-Oriented E-commerce Dialogue Generation with Decision Tree Branching** — Xiangci Li et al. (ACL 2025). [ACL Anthology](https://aclanthology.org/2025.acl-long.641/) | TRACER dùng decision-tree branching; tác giả cung cấp khoảng 3,6 nghìn hội thoại trên 3 miền cho hội thoại thương mại điện tử hướng mục tiêu. | Áp dụng nguyên tắc hỏi ngắn, có mục tiêu; không ép cây hội thoại cố định vì khách có thể đổi mục tiêu. |
| **SpokenWOZ: A Large-Scale Speech-Text Benchmark for Spoken Task-Oriented Dialogue Agents** — Shuzheng Si et al. (NeurIPS 2023). [NeurIPS paper](https://papers.nips.cc/paper_files/paper/2023/hash/7b16688a2b053a1b01474ab5c78ce662-Abstract-Datasets_and_Benchmarks.html) | 5.700 dialogues, khoảng 203 nghìn turns và 249 giờ audio ở 8 miền; setup paper báo cáo best end-to-end completion 52,1% và spoken TOD joint goal accuracy 25,65%. | Cho thấy text test không xác minh STT/TTS; không dùng SpokenWOZ score làm score cho kiosk tiếng Việt. |

### Lựa chọn kỹ thuật

1. Dùng pipeline dễ đo: chuẩn hóa query → lấy candidate → lọc hard constraint bằng code → xếp hạng → kiểm tra nguồn.
2. Không xem lời văn model là state hay bằng chứng giao dịch; chấm cả tool/state transition.
3. Cập nhật state từng phần thay vì tái tạo toàn bộ intent mỗi lượt.
4. Chỉ hỏi làm rõ khi thuộc tính thiếu ảnh hưởng lựa chọn.
5. Không thêm vector store/model agent/workflow nặng nếu chưa cải thiện benchmark cùng catalog.

## 4. Proposed and Implemented Architecture

### Trước

Giao diện gọi API hội thoại trung tâm. Policy, intent, grounding và một phần business flow tập trung trong main.py; không phải mọi module trong tài liệu đều có class/runtime tương ứng.

~~~mermaid
flowchart LR
  UI[Kiosk Vue] --> API[FastAPI main.py]
  API --> POLICY[Intent and policy checks]
  API --> AGENT[RetailAgent]
  AGENT --> TOOLS[Retail tools]
  TOOLS --> DB[(Catalog source)]
  API --> SESSION[Session store]
~~~

### Hiện tại

~~~mermaid
flowchart LR
  USER[Khách text hoặc touch] --> UI[Kiosk Vue]
  UI --> API[FastAPI API]
  API --> GUARD[Policy and action guards]
  GUARD --> FLOW[Shopping flow controller]
  GUARD --> AGENT[RetailAgent tool loop]
  FLOW --> MEMORY[ShoppingSessionState]
  AGENT --> TOOLS[Retail tools and validators]
  TOOLS --> DEMO[(Demo catalog)]
  TOOLS --> PG[(PostgreSQL read-only snapshot)]
  FLOW --> SHOP[In-memory cart and checkout sandbox]
  GUARD --> TICKET[Single-process demo support ticket]
  MEMORY -. production adapter .-> REDIS[(Redis shared sessions)]
  SHOP --> API
  TICKET --> API
  API --> UI
~~~

API vẫn điều phối nhiều logic. PostgreSQL `SELECT 1` timeout và Redis `PING` không kết nối được trong probe hiện tại. Production không nên dùng cart/ticket in-memory; snapshot không phải POS tồn live.

### Luồng dữ liệu

~~~mermaid
sequenceDiagram
  participant C as Khách
  participant K as Kiosk
  participant A as API policy/router
  participant S as Session state
  participant T as Retail tools
  participant D as Catalog
  participant H as Shopping sandbox
  C->>K: Hỏi, đổi tiêu chí hoặc chọn hàng
  K->>A: POST /agent/respond + session_id
  A->>S: Đọc state/constraint hiện tại
  A->>A: Policy + deterministic intent
  alt Tìm/tư vấn catalog
    A->>T: search/recommend/snapshot
    T->>D: Candidate/query có giới hạn
    D-->>T: Dữ kiện và provenance
    T-->>A: Observation đã validate
  else Cart/checkout
    A->>H: Cập nhật giỏ, quote hoặc order sandbox
    H-->>A: Trạng thái có cấu trúc
  end
  A->>S: Lưu state transition
  A-->>K: Text + state/cart/order/ticket
  K-->>C: Hiển thị phản hồi
~~~

## 5. Standard Retail Sales Flow

### Hành trình mua sắm

START SESSION → DISCOVER NEEDS → CLARIFY (nếu cần) → SEARCH / RECOMMEND → COMPARE / SELECT → CART → CONFIRM CHECKOUT → PAYMENT STATUS hoặc STAFF HANDOFF → CLOSE SESSION

Khách có thể vào thẳng câu hỏi giá/vị trí, đổi tiêu chí, hỏi lại sản phẩm, thêm nhiều món, hủy checkout, nhờ nhân viên hoặc kết thúc phiên. Flow không bắt buộc mọi bước.

### State và ràng buộc

Runtime lưu current intent, active product/category, constraints, selected/comparison IDs, cart reference, pending clarification/action/quantity/confirmation/order, handoff/payment status và state version.

- **SET:** thêm/cập nhật một tiêu chí.
- **REPLACE:** thay toàn bộ constraint khi bắt đầu lại.
- **REMOVE:** bỏ một tiêu chí.
- **KEEP:** giữ tiêu chí không đổi.

“Tìm snack không cay dưới 30 nghìn” rồi “đổi thành dưới 20 nghìn” giữ category=snack, spicy=false và thay budget thành LT 20.000 VND. API test xác nhận state này.

### Tra cứu và recommendation

- Giá/kệ/tồn/hạn dùng/thuộc tính chỉ được nói khi tool trả field tương ứng.
- Hard constraint như nhóm hàng, giá, độ cay và sellable stock lọc bằng code; brand/khẩu vị có thể là preference mềm.
- Thiếu nhãn dinh dưỡng hoặc promotion còn hạn thì agent nói chưa xác minh.
- Không có dữ liệu doanh số/đánh giá để xác nhận “bán chạy nhất”, “ngon nhất” hoặc “được yêu thích nhất”.

### Giỏ, checkout, payment, handoff

Cart dùng integer VND, kiểm tra session ownership, stock/expiry, số lượng, idempotency và tổng tiền ở backend. Checkout tạo quote/token có hạn, yêu cầu xác nhận rõ ràng rồi mới tạo order PENDING. Chỉ sự kiện sandbox được xác minh mới cập nhật status; câu khách tự báo đã chuyển khoản không làm order thành PAID. Không có ngân hàng/gateway thật và không gửi QR thật.

Yêu cầu hỗ trợ rõ ràng tạo ticket demo có ID, loại vấn đề, mô tả đã lọc contact data và status RECEIVED_DEMO. Response nói rõ chưa liên hệ nhân viên thật. Session cô lập theo session_id; idle TTL và close xóa memory hội thoại. Redis adapter có kiểm thử fail-closed nhưng chưa xác minh live. Cart/order/ticket chỉ bền trong process demo.

## 6. Implementation Details

| Vấn đề | Thay đổi | Bằng chứng |
|---|---|---|
| “Chuyển khoản xong” bị nhận thành câu hỏi sản phẩm | Mở rộng intent mẫu trong backend/services/shopping_flow.py để route CHECK_PAYMENT_STATUS. | API test dùng chính câu “Mình chuyển khoản xong rồi, kiểm tra giúp nhé”; response PENDING, chưa xác minh ngân hàng thật. |
| Injection “bỏ qua xác minh” có thể tạo ticket demo | Thêm marker trong main.py và support.py; chặn override trước shopping/support, không gọi tool hay tạo ticket. | Test xác nhận local policy, state không đổi, ticket null, tools_used rỗng. |
| “Tạo đơn … gửi QR” không được nhận là checkout | Thêm “tạo đơn/create order”; giỏ trống trả lời yêu cầu thêm món, không tạo QR hay claim giao dịch. | Ba ca live về cart, empty checkout và demo handoff đạt 3/3. |
| Grader sai ở recommendation deterministic | evaluation/live_text_conversation_benchmark.py đọc session state và kiểm constraint/cart/order/ticket/tool/response. | Bộ v2 kiểm tra category=snack, spicy=false, LT 20.000 và transaction fields. |
| Default cases cũ khẳng định tính năng chưa hỗ trợ | Đổi sang cart write, checkout giỏ trống, demo handoff; cập nhật evaluation/README.md. | Default live cases pass 3/3; file v1 và kết quả cũ được giữ nguyên. |
| Thiếu field dễ bị bịa | Grounding layer render snapshot/tool observations; policy từ chối thuộc tính chưa có. | v2 kiểm nutrition thiếu dữ liệu và false payment; P0 kiểm false-paid/expired stock. |
| Đổi lựa chọn qua nhiều lượt | ShoppingSessionState và thao tác constraint; parser ghép state cũ với query mới. | API multi-turn test và live grader đọc state để assert. |
| Giỏ/hỗ trợ bị hiểu như thật | ShoppingService, SupportTicketStore đánh dấu sandbox/demo; commerce write chỉ dùng demo catalog. | Sandbox 0 false-paid; cross-session checks không leakage. |
| Snapshot bị hiểu nhầm là store feed | Importer quarantine dữ liệu lỗi, tools gắn source/freshness; campaign/receipt/POI cần feed riêng. | 3.099 rows, 3.081 accepted, 18 quarantine; không dùng promotion text cũ để báo campaign active. |
| Planogram bị hiểu là vị trí vật lý | Phân biệt RECOMMENDED_PLANOGRAM và SIMULATED; ảnh theo dữ liệu gán SKU/aisle. | Vị trí là đề xuất, chưa xác minh tại cửa hàng. |

## 7. Evaluation Methodology

### Môi trường

- Windows; Python 3.13.15; Node 24.21.0; npm 11.19.0.
- Frontend dùng Vite 8.2.2 và Vitest 4.1.11.
- API test chạy CATALOG_REPOSITORY=demo với 6 SKU.
- data/winmart_products.json: 3.099 rows, 3.081 accepted, 18 quarantined; SHA-256 a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6.
- Live v2 chỉ text tiếng Việt; không mở mic, gọi STT/TTS hoặc đo browser streaming.
- Model-only smoke dùng Qwen qwen/qwen3.8-27b, fallback tắt. Live v2 gọi Groq thật qua API local ASGI; local intent/catalog xử lý phần deterministic.

### Phân biệt kết quả

| Loại | Chứng minh | Không chứng minh |
|---|---|---|
| Unit/API | Parser, state, ownership, policy, tool contract, sandbox. | Hội thoại khách thật hoặc availability ngoài test. |
| P0 regression | 8 nhóm lỗi × 5 lượt; response giả lập. | Model tự chọn tool hay độ tự nhiên. |
| Shopping sandbox | API workflow/cart/order/payment/session, không gọi LLM. | Latency mạng, DB production, payment thật. |
| Live Groq text | Provider thật trên tập nhỏ; grader kiểm dữ kiện/state. | Hallucination rate đại diện, giọng nói, browser UX hoặc model planning tổng quát. |
| Frontend unit/build | Component test, TypeScript, bundle build. | E2E browser, mic/camera, kiosk touch thiết bị thật. |
| Provider probe | Groq model-list trả 200; key chỉ được kiểm sự hiện diện, không in giá trị. | Availability liên tục hoặc quota trong tương lai. |

## 8. Experimental Results

### Baseline trước follow-up trên code commit 1757bbd

| Kiểm tra | Kết quả | Giới hạn |
|---|---:|---|
| Backend pytest | **490 passed** | Catalog demo, in-process. |
| Frontend Vitest | **41 passed / 5 files** | JSDOM cảnh báo media/canvas chưa được giả lập. |
| Vue TypeScript + production build | **Pass** | Vite cảnh báo chunk model-viewer 1.024,05 kB (gzip 290,30 kB); main JS 168,28 kB (gzip 55,29 kB). |
| Ruff | **Pass** cho backend/tests/evaluation | Quét toàn workspace có 6 lỗi format trong .tmp/check_voice_repair.py; file tạm chưa track được giữ nguyên, không commit. |
| compileall / git diff --check | **Pass** | Có cảnh báo chuẩn hóa LF/CRLF trên Windows. |
| P0 regression | **40/40 pass** (8 × 5) | Scripted/fake responses. Artifact: evaluation/results/p0_regression_20261019T085321Z.json. |
| Shopping sandbox | **20/20 pass**, 98 API calls, 0 LLM calls | 4 false-payment check, 0 false confirmation; 4 cross-session check, 0 leak. P50 1.833 ms, P95 12.551 ms, mean 5.351 ms in-process; không phải latency mạng. |
| Live text v2 | **13/13 pass**, 7 hội thoại; 2 provider requests, 2 tool calls | P50 9 ms, P95 nearest-rank 9.693 ms; đuôi dài do Groq. 4.646 prompt / 79 completion tokens. Các lời gọi tool từ model chưa có expected-tool assertion riêng trong bộ này. Mẫu nhỏ. |
| Default live cases sau cập nhật | **3/3 pass** | Thêm một SKU, checkout giỏ trống, handoff demo; không gọi LLM. |
| Live Groq model-only smoke | **5/5 HTTP 200**, Qwen trả 4 provider calls; fallback/rate-limit 0 | Provider median 751 ms; API P95 17.787 ms có pacing 9 giây, P95 bỏ pacing 2.600 ms. 9.271 prompt / 156 completion tokens. 2 trường price/shelf đạt 2/2 minimum-field grade. Không đo first token. |

### Trước/sau và triage

- Baseline commit 2f8b036 có 487 backend pass và 41 frontend pass. Sau regression injection và checkout, code cuối có 490 backend pass.
- Lần v2 đầu đạt 10/13 theo grader cũ. Một assertion là false negative: grader yêu cầu arguments trong LLM trace trong khi recommendation chạy deterministic và session constraints đúng. Hai lỗi thật là phrase “chuyển khoản xong” và injection “bỏ qua xác minh”. Sau sửa parser, policy gate và state-aware grader, đạt 13/13.
- Lần đầu chạy 3 default cases đạt 2/3; “Tạo đơn … QR” không được route sang checkout. Thêm “tạo đơn/create order”; chạy lại đạt 3/3, không phát QR hay claim thanh toán.
- Smoke benchmark follow-up đầu tiên báo 16 PASS / 7 FAIL / 7 UNSUPPORTED vì grader còn đòi trace tool cũ, format giá không dấu chấm và tool observation dù policy chặn trước model. Harness được sửa để kiểm tra route recommendation xác định, dữ kiện trả ra, constraint đã áp dụng và chặn local; lần kế đạt 23 PASS / 0 FAIL / 7 UNSUPPORTED. Audit sâu sau đó tìm thấy bốn mục bị gắn UNSUPPORTED dù controller đã hỗ trợ: thêm/xem giỏ, tham chiếu món trong giỏ, payment status sandbox và Redis adapter tồn tại. Smoke tiếp theo kiểm tra ba flow qua API; Redis live đổi thành NOT RUN, kết quả 26 PASS / 0 FAIL / 3 UNSUPPORTED / 1 NOT RUN. Bổ sung comparison controller và coverage giá/tồn/follow-up làm kết quả mới nhất thành 27 PASS / 0 FAIL / 3 UNSUPPORTED / 1 NOT RUN. Regression cho thấy câu hỏi “món nào ngon/bán chạy nhất” từng bị detector so sánh bắt nhầm; điều kiện follow-up được siết và toàn bộ test ranking/comparison hiện pass. Ba mục chưa hỗ trợ là FEFO theo batch, product knowledge runtime và RAG.
- Sandbox benchmark cũ cũng 20/20. Chênh lệch timing in-process giữa lần chạy không đại diện hiệu năng production.
- 13/13 là assertion định trước (ID, phrase, state, cart, order, ticket), không phải chấm thủ công naturalness và không có nghĩa hallucination rate bằng 0.

Artifact mới được giữ riêng, không ghi đè lịch sử:

- evaluation/results/live_text_retail_v2_final_20261019.json
- evaluation/results/live_text_retail_v2_after_fixes_20261019.json
- evaluation/results/live_text_retail_v2_20261019.json
- evaluation/results/live_text_retail_default_cases_20261019.json
- evaluation/results/live_text_retail_default_cases_after_fixes_20261019.json
- evaluation/results/p0_regression_20261019T100745Z.json
- evaluation/results/shopping_flow_sandbox_20261019T100744Z.json
- evaluation/results/smoke_benchmark_coverage_20261019_postpush_d64b751.json
- evaluation/results/live_groq_model_after_shopping_20261019.json
- evaluation/results/smoke_benchmark_followup_20261019.json (runner cũ, diagnostic được giữ nguyên)
- evaluation/results/smoke_benchmark_followup_20261019_final.json (runner đã sửa)
- evaluation/results/smoke_benchmark_coverage_20261019.json (coverage đã đối chiếu lại với runtime)

## 9. Error Analysis

| Nhóm | Lỗi/rủi ro còn lại | Xử lý hoặc bước cần làm |
|---|---|---|
| Intent | Cụm thanh toán có biến thể vùng miền; trước đây “chuyển khoản xong” rơi vào product intent. | Đã thêm mẫu và test. Mở rộng theo hội thoại gán nhãn, tránh regex quá rộng. |
| State tracking | Grader cũ bỏ sót deterministic state; parser còn dựa trên cụm đã biết. | Grader đọc session state; API test giữ category/spicy, thay budget. Thêm typo/ASR/no-diacritic/conflict cases. |
| Retrieval | Demo live chỉ 6 SKU; retrieval PostgreSQL trên 3.099 dòng không chạy được. | Chưa kết luận Recall@k/typo quality; chạy lại khi DB hoạt động. |
| Grounding | Snapshot có ngày cũ; giá demo có thể khác quầy hiện tại. | Gắn freshness khi có; cần feed giá/tồn theo chi nhánh và SLA. Thiếu field thì không khẳng định. |
| Recommendation | Không có doanh số/ratings để xếp “bán chạy nhất/ngon nhất”. | Chỉ gợi ý theo constraint có dữ liệu; chưa có analytics khách hàng/thị trường. |
| Database | PostgreSQL read-only probe timeout; Redis endpoint trả connection error. | Chưa chạy query/session integration live; unit adapter không thay cho vận hành thật. |
| Payment | Không có bank webhook. | Sandbox giữ PENDING; LLM không đặt PAID. Chưa có payment provider, không gửi QR thật. |
| Customer support | Ticket simulator không liên hệ nhân viên/CRM. | Response ghi rõ demo; cần queue có ack nếu triển khai handoff thật. |
| Security | Guard chặn các injection đã nhận diện, gồm bypass verification. | Chưa penetration test, chưa đánh giá injection gián tiếp từ feed/catalog. |
| UI/voice | Text UI nhận SSE delta sau khi backend hoàn tất kiểm tra; chưa đo browser/mic/TTS/lip-sync thực. Lượt browser E2E trên `127.0.0.1` bị URL security policy từ chối nên không chạy. | SSE không stream token khi model đang sinh; cần browser E2E, latency tới first validated text/audio trên thiết bị thật. |
| Latency | Median live thấp do nhiều route local; một query Groq mất khoảng 9,7 giây. | Tách provider/local ASGI; cần benchmark nhiều lượt trên mạng/thiết bị thật. |

## 10. Limitations and Future Work

### Trước demo dùng dữ liệu cửa hàng thật

1. Khôi phục PostgreSQL và Redis; chạy catalog, session, TTL, state ownership và store routing trên staging.
2. Nạp store master/kiosk mapping, giá/tồn theo chi nhánh, campaign có hạn/terms, receipt event và vị trí kệ đã kiểm chứng. Catalog web không xác nhận stock chi nhánh.
3. Thay in-memory cart/order/ticket bằng storage transactional bền vững; test retry đồng thời và multi-replica.
4. Nếu có thanh toán, dùng sandbox provider và xác minh signature/idempotency/replay/expiry trước production.
5. Chỉ tích hợp CRM/staff queue khi có API ack; simulator phải giữ nhãn demo.

### Cải tiến chất lượng

- Mở rộng lên hàng trăm tác vụ tiếng Việt do người Việt viết/đọc lại; phân tầng Bắc/Trung/Nam, dấu/không dấu, ASR noise và sửa ý.
- Chấm riêng intent, entity, state update, retrieval, hard constraint, grounding, tool selection, handoff và naturalness.
- Paired before/after cùng dataset/catalog/version; lưu raw JSON, grader version, git SHA.
- Browser e2e cho chat/cart/session reset, streaming, mic permission, chọn device, STT/TTS cleanup và lip-sync.
- Tối ưu model-viewer chunk sau khi đo trên thiết bị thật.
- Chỉ làm RAG chính sách khi có tài liệu được duyệt, provenance/version và retrieval benchmark.

### Chưa xác minh

Không có load test production, payment thật, POS, PostgreSQL/Redis live, staff CRM, microphone/TTS, network latency, user study hay full BFCL/ShoppingBench/Shopping Reasoning Bench. Không tuyên bố production-ready.

## 11. Reproducibility

### Mã nguồn và cấu hình

- Branch `codex/session-isolation-ui-hardening`; code follow-up được benchmark gần nhất là commit `d64b7514b3ee415c3d868085607af651ef571a12`. Các artifact P0, shopping sandbox và smoke nêu ở trên được tạo sau commit này. Artifact shopping ghi `git_worktree_dirty=true` vì báo cáo đang được cập nhật và còn file benchmark/scratch untracked; tracked application/test code không thay đổi tại thời điểm chạy.
- Base commit của phần follow-up: `98ee7ef8e416cde3f2afd3bf0cc4845fe78d485f`.
- Báo cáo đánh giá cũ giữ riêng tại `docs/reports/archive/`; follow-up được commit trên nhánh feature này.
- Runtime test dùng catalog demo với CATALOG_REPOSITORY=demo.
- Live Groq cần key trong .env; báo cáo và artifact không chứa giá trị key.
- PostgreSQL/Redis live probe không khả dụng; không dùng chúng để tạo kết quả pass giả.
- Python 3.13.15, Node 24.21.0, npm 11.19.0.

### Lệnh kiểm tra

~~~powershell
$env:CATALOG_REPOSITORY = 'demo'
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\ruff.exe check backend tests evaluation
.venv\Scripts\python.exe -m compileall -q backend evaluation tests
git diff --check

cd apps\kiosk-airi
npm run test:run
npm run build
~~~

### Lệnh benchmark và demo

~~~powershell
$env:CATALOG_REPOSITORY = 'demo'
.venv\Scripts\python.exe evaluation\run_p0_regressions.py --trials-per-case 5
.venv\Scripts\python.exe evaluation\run_shopping_flow_benchmark.py --trials 2
.venv\Scripts\python.exe evaluation\run_smoke_benchmark.py --output evaluation\results\smoke_benchmark_coverage_20261019_postpush_d64b751.json
.venv\Scripts\python.exe -m evaluation.live_text_conversation_benchmark --case-file evaluation\cases\vietnamese_retail_shopping_v2.json --pace-seconds 9 --output evaluation\results\live_text_retail_v2_reproduced.json
.venv\Scripts\python.exe -m evaluation.run_product_search_benchmark --cases evaluation\cases\product_resolution_v2.jsonl --output evaluation\results\product_search_benchmark_v2.json
~~~

Demo: terminal backend đặt CATALOG_REPOSITORY=demo rồi chạy uvicorn backend.api.main:app --reload; terminal frontend chạy npm run dev trong apps/kiosk-airi. Demo không thay database URL hay bật giao dịch thật.

Artifact tái lập nằm trong evaluation/results với timestamp ở mục 8. Dùng tên output mới, không ghi đè lịch sử. Báo cáo customer-care cũ được giữ tại docs/reports/archive/.

## 12. References

1. Ye, Jingheng et al. 2024. **ProductAgent: Benchmarking Conversational Product Search Agent with Asking Clarification Questions.** arXiv:2407.00942. https://arxiv.org/abs/2407.00942
2. Wang, Jiangyuan et al. 2025. **ShoppingBench: A Real-World Intent-Grounded Shopping Benchmark for LLM-based Agents.** arXiv:2508.04266. https://arxiv.org/abs/2508.04266
3. Fan, Shuxian et al. 2026. **Shopping Reasoning Bench: An Expert-Authored Benchmark for Multi-Turn Conversational Shopping Assistants.** arXiv:2606.12608. https://arxiv.org/abs/2606.12608
4. Berkeley Gorilla Team. 2025. **Berkeley Function-Calling Leaderboard V4.** https://gorilla.cs.berkeley.edu/leaderboard and https://gorilla.cs.berkeley.edu/blogs/15_bfcl_v4_web_search.html
5. Nguyen Van, Phi et al. 2022. **ViWOZ: A Multi-Domain Task-Oriented Dialogue Systems Dataset For Low-resource Language.** arXiv:2203.07742. https://arxiv.org/abs/2203.07742
6. Lesci, Pietro et al. 2023. **Diable: Efficient Dialogue State Tracking as Operations on Tables.** Findings of ACL 2023, DOI 10.18653/v1/2023.findings-acl.615. https://aclanthology.org/2023.findings-acl.615/
7. Li, Xiangci et al. 2025. **Wizard of Shopping: Target-Oriented E-commerce Dialogue Generation with Decision Tree Branching.** ACL 2025. https://aclanthology.org/2025.acl-long.641/
8. Si, Shuzheng et al. 2023. **SpokenWOZ: A Large-Scale Speech-Text Benchmark for Spoken Task-Oriented Dialogue Agents.** NeurIPS Datasets and Benchmarks 2023. https://papers.nips.cc/paper_files/paper/2023/hash/7b16688a2b053a1b01474ab5c78ce662-Abstract-Datasets_and_Benchmarks.html
9. W3C. **Web Content Accessibility Guidelines (WCAG) 2.2, Target Size (Minimum), Success Criterion 2.5.8.** https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum
10. W3C. **Web Content Accessibility Guidelines (WCAG) 2.2, Target Size (Enhanced), Success Criterion 2.5.5.** https://www.w3.org/WAI/WCAG22/Understanding/target-size-enhanced
11. Yang, Xinwei et al. 2026. **RealWorldShop: Benchmarking and Improving Conversational Shopping Agents in Real-World E-commerce.** arXiv:2609.38974v1, preprint submitted 2026-09-30. https://arxiv.org/abs/2609.38974
