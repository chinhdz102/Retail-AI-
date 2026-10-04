# Retail evaluation

Các script này kiểm tra những phần đã có trong `Retail_AI_Evaluation_Test_Plan.md` và roadmap P0. Chúng không thay thế bộ 120–180 scenario hoặc đánh giá người dùng.

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

`run_smoke_benchmark.py` dùng phản hồi Groq được lập trình sẵn để kiểm backend, tool, grounding, phân quyền, session và lỗi. Kết quả ở `evaluation/results/smoke_benchmark.json`; exit code 1 nghĩa là có ca FAIL.

`run_p0_regressions.py` chạy 8 regression ưu tiên × 5 lượt. Nó cũng dùng phản hồi giả lập nên cô lập backend và không tiêu quota; nó không đo khả năng model tự chọn tool hoặc `fields`. Kết quả ở `evaluation/results/p0_regression_5x.json`.

`live_groq_smoke.py` chạy benchmark agent bằng Groq thật. Nó mặc định đọc key từ `.env`; có thể chọn file cấu hình khác bằng `RETAIL_EVAL_ENV_FILE`. Script không ghi key vào kết quả, tắt SDK retry, đặt timeout provider 12 giây và timeout tác vụ 50 giây. Kết quả được lưu sau mỗi lượt.

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

`live_groq_stt_benchmark.py` đo Groq STT trên audio có transcript tham chiếu. Nó nhận file audio bên ngoài repo để không lưu audio vào codebase, chạy 3 lượt mặc định, ghi model thực sự trả lời, fallback trace, latency, WER và CER nếu có transcript chuẩn. Có thể truyền transcript dài bằng `--reference-file`; timestamp dạng `[00:00:00]` sẽ được bỏ khi tính lỗi. Dùng `--omit-transcription` khi chỉ cần đo tốc độ mà không lưu nội dung nhận dạng vào báo cáo. Ví dụ với một clip tiếng Việt:

```powershell
.\.venv\Scripts\python.exe -m evaluation.live_groq_stt_benchmark `
  --audio "$env:TEMP\retail_groq_stt_fleurs_vi_0000.wav" `
  --reference "văn hóa và bộ lạc cổ xưa đã bắt đầu giữ những con vật này để dễ lấy sữa tóc thịt và da" `
  --source "Google FLEURS vi_vn_0000 (CC-BY 4.0)" `
  --trials 3
```

Kết quả mẫu ngày 01/10/2026 ở `evaluation/results/live_groq_stt_benchmark.json`; báo cáo tổng hợp nằm trong `evaluation/results/evaluation_report.md`. WER/CER được chuẩn hóa chữ hoa/thường, dấu câu và Unicode; WER tách từ theo khoảng trắng. Một audio lặp nhiều lượt chỉ hữu ích để đo độ ổn định latency, chưa phải đánh giá độ chính xác đại diện.

Báo cáo sửa P0 nằm tại `evaluation/results/evaluation_report.md`.
