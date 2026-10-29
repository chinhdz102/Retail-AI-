# Retail evaluation

Các script này kiểm tra những phần đã có trong `Retail_AI_Evaluation_Test_Plan.md` và roadmap P0. Chúng không thay thế bộ 120–180 scenario hoặc đánh giá người dùng.

The default `pytest` suite is an offline system test. `tests/conftest.py` forces
`CATALOG_REPOSITORY=demo` and blanks local Groq key variables so a developer's
`.env` cannot silently turn unit/API tests into PostgreSQL integration tests or
send test traffic to Groq. PostgreSQL connectivity and catalog search must be
tested separately with `evaluation.run_product_search_benchmark`; do not treat
the demo fixture as evidence that PostgreSQL is available.

Chạy từ thư mục gốc repo:

```powershell
.\.venv\Scripts\python.exe evaluation\run_smoke_benchmark.py
.\.venv\Scripts\python.exe evaluation\run_p0_regressions.py
.\.venv\Scripts\python.exe -m evaluation.live_groq_smoke
.\.venv\Scripts\python.exe -m evaluation.run_product_search_benchmark
```

`run_product_search_benchmark.py` đo riêng bộ phân giải sản phẩm PostgreSQL, không gọi LLM. Bộ ca chuẩn ở `evaluation/cases/product_resolution.jsonl` gồm ID/barcode, tên có dấu/không dấu, partial match, brand và typo. Script chạy warm-up, lặp từng truy vấn 3 lần, ghi Recall@1/3, MRR, latency, revision Git và SHA-256 dataset. Chọn PostgreSQL qua `DATABASE_URL` trong `.env` hoặc `--database-url`; không đưa chuỗi kết nối vào báo cáo. Trước lần chạy đầu sau cập nhật code, chạy lại importer để cập nhật `search_title` và migration schema:

```powershell
.\.venv\Scripts\python.exe scripts\import_winmart.py --apply
.\.venv\Scripts\python.exe -m evaluation.run_product_search_benchmark
```

Mốc Recall@1 ≥ 90%, Recall@3 ≥ 97% và median search < 100 ms là mục tiêu của roadmap trên catalog 3.099 SKU; kết quả ở máy local cần điền vào report benchmark để xác nhận.

`product_resolution_v2.jsonl` mở rộng từ 12 lên 23 truy vấn, thêm tiếng lóng “bim bim/bimbim”, phân biệt truy vấn “bỉm” có dấu với snack, và các biến thể đồ uống, hóa mỹ phẩm, thực phẩm, sữa chua. Ca mới được kiểm tra tham chiếu tới product ID trong snapshot đã được chấp nhận. Chạy riêng bộ v2 để đo resolver PostgreSQL hiện tại:

```powershell
.venv\Scripts\python.exe -m evaluation.run_product_search_benchmark `
  --cases evaluation/cases/product_resolution_v2.jsonl `
  --output evaluation/results/product_search_benchmark_v2.json
```

Kiểm thử SQL giả lập và đối chiếu ID snapshot không thay thế benchmark truy vấn PostgreSQL thật; nếu DB không kết nối được, báo cáo kết quả runtime là chưa đo.

`run_smoke_benchmark.py` dùng phản hồi Groq được lập trình sẵn để kiểm backend, tool, grounding, phân quyền, session và lỗi. Kết quả ở `evaluation/results/smoke_benchmark.json`; exit code 1 nghĩa là có ca FAIL.

`run_p0_regressions.py` chạy 8 regression ưu tiên × 5 lượt. Nó cũng dùng phản hồi giả lập nên cô lập backend và không tiêu quota; nó không đo khả năng model tự chọn tool hoặc `fields`. Kết quả ở `evaluation/results/p0_regression_5x.json`.

`live_groq_smoke.py` chạy benchmark agent bằng Groq thật. Nó mặc định đọc key từ `.env`; có thể chọn file cấu hình khác bằng `RETAIL_EVAL_ENV_FILE`. Script không ghi key vào kết quả, tắt SDK retry, đặt timeout provider 12 giây và timeout tác vụ 50 giây. Kết quả được lưu sau mỗi lượt.

`live_text_conversation_benchmark.py` kiểm tra các câu hỏi tiếng Việt kiểu hội thoại, hội thoại nhiều lượt, sản phẩm demo, dữ kiện thiếu, sản phẩm hết hạn, yêu cầu giữ hàng/sửa giá và bộ case tùy chỉnh. Benchmark gọi Groq thật nhưng chỉ dùng text; phần tự nhiên vẫn cần người đọc chấm theo rubric trong JSON. Report ghi runtime repository và đánh dấu lượt tìm kiếm PostgreSQL thật nếu có.

Bộ hội thoại tiếng Việt mới nằm trong `evaluation/cases/vietnamese_text_conversation_v1.json`. Nó chỉ gửi câu tiếng Việt và chấm riêng việc model có chọn đúng tool, đưa đúng tham số, tránh tool khi chỉ chào hỏi/cảm ơn, giữ tham chiếu qua các lượt và từ chối bịa dữ kiện. Chạy bằng Groq thật:

```powershell
.\.venv\Scripts\python.exe -m evaluation.live_text_conversation_benchmark `
  --case-file evaluation/cases/vietnamese_text_conversation_v1.json `
  --pace-seconds 12 `
  --output evaluation/results/live_vietnamese_conversation.json
```

Pacing 12 giây giúp giữ nhịp dưới quota TPM đã quan sát ở các lần chạy trước. Kết quả tự động chỉ là coverage probe trên sáu sản phẩm demo; độ tự nhiên, cách hỏi làm rõ và đúng ngữ cảnh vẫn cần người đọc nghe/đọc lại từng lượt.

`vietnamese_retail_shopping_v2.json` là bộ hiện hành cho luồng mua sắm đầu-cuối: bảy hội thoại, 13 lượt, gồm nối tiếp giá/kệ/tồn, đổi ràng buộc, thiếu dữ kiện dinh dưỡng, thêm nhiều món vào giỏ, checkout và lời khách tự báo đã chuyển khoản, yêu cầu hỗ trợ demo, cùng injection không được tạo ticket. Grader kiểm tra cả response API và state đang lưu trong session (constraint, giỏ, order, ticket), vì nhánh catalog xác định có thể gọi tool mà không phát model tool-call. File `vietnamese_text_conversation_v1.json` và các kết quả cũ vẫn được giữ để tái lập lịch sử; không dùng các assertion của v1 làm kết luận về checkout/handoff hiện tại.

Chạy bộ v2 bằng Groq thật trên catalog demo; dùng tên output mới để không ghi đè kết quả cũ:

```powershell
$env:CATALOG_REPOSITORY = 'demo'
.venv\Scripts\python.exe -m evaluation.live_text_conversation_benchmark `
  --case-file evaluation/cases/vietnamese_retail_shopping_v2.json `
  --pace-seconds 9 `
  --output evaluation/results/live_text_retail_v2_reproduced.json
```

Đây là probe nhỏ có assertion về dữ liệu và policy, không thay thế đánh giá người đọc về độ tự nhiên, benchmark hàng trăm hội thoại, hoặc kiểm thử trình duyệt/STT/TTS.

`vietnamese_safety_and_injection_v1.json` bổ sung câu hỏi hỗ trợ thường gặp và prompt injection tiếng Việt: giả quyền quản trị, đòi lộ prompt/key/session, tool giả, câu không dấu, chỉ dẫn được trích dẫn và bẻ lái giá qua nhiều lượt. Bộ này dùng cùng runner Groq thật; trace cuối ngày 18/10/2026 ở `evaluation/results/live_vietnamese_safety_injection_final_20261018.json`. Chạy lại bằng:

```powershell
.\.venv\Scripts\python.exe -m evaluation.live_text_conversation_benchmark `
  --case-file evaluation/cases/vietnamese_safety_and_injection_v1.json `
  --pace-seconds 12 `
  --output evaluation/results/live_vietnamese_safety_injection.json
```

Các prompt lộ thông tin nội bộ và thao tác quản trị bị chặn bằng local policy, nên lượt đó xác nhận luồng backend chặn trước model; nó không đo khả năng Qwen tự từ chối một jailbreak chưa được nhận diện. Injection gián tiếp qua dữ liệu catalog bên ngoài vẫn cần đánh giá riêng.

`vietnamese_product_consultation_grounded_v1.json` kiểm tra catalog WinMart thật với Qwen: hỏi đúng biến thể và giá, hỏi brand mơ hồ, gộp giá với lợi ích sức khỏe, hỏi thành phần, công dụng và sức khỏe khi các field không có trong data. Lần chạy ngày 18/10/2026 đạt 6/6; report `evaluation/results/live_vietnamese_product_consultation_final_20261018.json` xác nhận `runtime_repository=postgres` và 4 lượt gọi tìm catalog PostgreSQL. Cách chạy:

```powershell
.\.venv\Scripts\python.exe -m evaluation.live_text_conversation_benchmark `
  --case-file evaluation/cases/vietnamese_product_consultation_grounded_v1.json `
  --pace-seconds 12 `
  --output evaluation/results/live_vietnamese_product_consultation.json
```

Lần chạy trên dùng pacing 12 giây để tránh giới hạn TPM. Median turn latency có cả pacing; xem `llm_trace[].latency_ms` để biết thời gian của từng lần gọi model. Bối cảnh, giới hạn dữ liệu và hướng bổ sung product knowledge nằm trong `docs/product-consultation-grounding.md`.

Chọn một nhóm ca để chạy lại sau thay đổi:

```powershell
.\.venv\Scripts\python.exe -m evaluation.live_text_conversation_benchmark `
  --pace-seconds 9 `
  --cases catalog_snack_recommendation multi_turn_product_reference
```

Báo cáo nghiên cứu và kết quả text ngày 13/10/2026 nằm ở `evaluation/results/text_conversation_review_20261013.md`.

Các probe `A4_nutrition_unsupported`, `N1_casual_drink` và `N2_reservation_request` kiểm tra thuộc tính catalog chưa có, cách hỏi gián tiếp và yêu cầu giữ hàng. Trace ghi câu trả lời cùng tool call; độ tự nhiên cần người đọc chấm, benchmark không tự động coi một câu trả lời an toàn nhưng né câu hỏi là thành công. Bản đánh giá ngày 04/10/2026 ở `evaluation/results/answer_quality_assessment_20261004.md`.

Có hai chế độ riêng:

- `system`: giữ fallback bật để đo khả năng hoàn thành tác vụ end-to-end.
- `model`: khóa model chính đã cấu hình và tắt fallback, để chấm riêng lựa chọn tool/field của model đó.

Ví dụ chạy system benchmark 12 scenario × 3 lượt và một lượt model-only cho ba câu hỏi fact cơ bản:

```powershell
.\.venv\Scripts\python.exe -m evaluation.live_groq_smoke `
  --mode system --trials-per-scenario 3 `
  --output evaluation/results/live_groq_smoke_system.json

.\.venv\Scripts\python.exe -m evaluation.live_groq_smoke `
  --mode model --scenarios A1_price A2_stock A3_shelf `
  --trials-per-scenario 1 --pace-seconds 9 `
  --output evaluation/results/live_groq_smoke_qwen.json
```

Yêu cầu được gửi tuần tự; mặc định cách nhau 9 giây giữa các lượt gọi agent để hạn chế chạm TPM. Điều chỉnh bằng `--pace-seconds` theo quota hiện tại của project. Pacing áp dụng giữa các lượt gọi agent, không giãn từng model attempt bên trong fallback. Có thể giới hạn scenario bằng `--scenarios`; `--output` giữ baseline riêng.

Kết quả ghi HTTP/API latency, latency đã bỏ pacing, latency từng model call, LLM/tool calls mỗi tác vụ, token usage, fallback attempts, rate-limit headers và grader field tối thiểu cho các ca price/stock/shelf. API latency tổng có tính pacing; dùng `median_api_latency_excluding_pacing_ms` để xem thời gian xử lý agent. Latency benchmark không tính ghi âm, STT, TTS, trình duyệt hoặc thời gian đến token đầu tiên.

Tiếp tục một lượt live bị ngắt bằng `--resume`:

```powershell
.\.venv\Scripts\python.exe -m evaluation.live_groq_smoke --resume
```

Chỉ tiếp tục khi mode, số lượt, khoảng pacing và scenario set khớp với file kết quả. Chạy lại không có `--resume` sẽ tạo baseline mới.

`live_groq_stt_benchmark.py` đo Groq STT trên audio bên ngoài repo, mặc định chạy 3 lượt và ghi model/fallback/latency. M4A/MP4 lấy duration từ movie header. Chỉ truyền `--reference` hoặc `--reference-file` khi có transcript nguyên văn đầy đủ cho toàn bộ audio; caption tóm tắt hoặc transcript thiếu đoạn sẽ làm WER/CER sai lệch. Timestamp dạng `[00:00:00]` được bỏ trước khi chấm. Dùng `--omit-transcription` để không lưu nội dung nhận dạng vào báo cáo. Ví dụ với một clip tiếng Việt:

```powershell
.\.venv\Scripts\python.exe -m evaluation.live_groq_stt_benchmark `
  --audio "$env:TEMP\retail_groq_stt_fleurs_vi_0000.wav" `
  --reference "văn hóa và bộ lạc cổ xưa đã bắt đầu giữ những con vật này để dễ lấy sữa tóc thịt và da" `
  --source "Google FLEURS vi_vn_0000 (CC-BY 4.0)" `
  --trials 3
```

Kết quả mẫu ngày 01/10/2026 ở `evaluation/results/live_groq_stt_benchmark.json`; báo cáo tổng hợp nằm trong `evaluation/results/evaluation_report.md`. WER/CER được chuẩn hóa chữ hoa/thường, dấu câu và Unicode; WER tách từ theo khoảng trắng. Một audio lặp nhiều lượt chỉ hữu ích để đo độ ổn định latency, chưa phải đánh giá độ chính xác đại diện.

`live_azure_tts_benchmark.py` gọi Azure Speech thật và đo riêng synth latency, thời gian nhận chunk audio đầu tiên và thời gian stream hoàn tất. Transcript không được ghi vào artifact; key chỉ đọc từ `.env`. Chạy hai lượt tiếng Việt:

```powershell
.\.venv\Scripts\python.exe evaluation\live_azure_tts_benchmark.py `
  --language vi --trials 2
```

Artifact JSON có timestamp, voice, loại audio, số byte và latency từng lượt. Kết quả provider-level không đo thời gian phát trên browser hoặc thiết bị, chất lượng phát âm, micro hay lip-sync.

Báo cáo sửa P0 nằm tại `evaluation/results/evaluation_report.md`.

For human answer-quality review, follow `evaluation/vietnamese_answer_quality_annotation_guide.md`. The current exporter fingerprints the frozen visible packet and produces separate annotator queues; `evaluation.score_vietnamese_answer_quality_review` checks both queues, reports per-dimension ordinal and per-flag agreement, and can incorporate a separate third-reviewer adjudication CSV without overwriting the original judgments. It refuses unreviewed queues. The currently retained packet has no completed labels, so it cannot produce human quality metrics yet.
