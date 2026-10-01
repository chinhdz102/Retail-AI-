# Retail evaluation

Các script này kiểm các phần đã có của `Retail_AI_Evaluation_Test_Plan.md` và
roadmap P0. Chúng không thay thế bộ 120–180 scenario hoặc đánh giá người dùng.

Chạy từ thư mục gốc repo:

```powershell
.\.venv\Scripts\python.exe evaluation\run_smoke_benchmark.py
.\.venv\Scripts\python.exe evaluation\run_p0_regressions.py
.\.venv\Scripts\python.exe -m evaluation.live_groq_smoke
```

`run_smoke_benchmark.py` dùng phản hồi Groq được lập trình sẵn để kiểm backend,
tool, grounding, phân quyền, session và lỗi. Kết quả ở
`evaluation/results/smoke_benchmark.json`; exit code 1 nghĩa là có ca FAIL.

`run_p0_regressions.py` chạy 8 regression ưu tiên × 5 lượt. Nó cũng dùng phản
hồi giả lập nên cô lập backend và không tiêu quota; nó không đo khả năng model
tự chọn tool hoặc `fields`. Kết quả ở
`evaluation/results/p0_regression_5x.json`.

`live_groq_smoke.py` chạy 9 scenario × 3 lượt bằng Groq thật, ghi model, latency,
tool arguments và fallback trace vào `evaluation/results/live_groq_smoke.json`.
Nó mặc định đọc key từ `.env`; có thể chọn file cấu hình khác bằng biến
`RETAIL_EVAL_ENV_FILE`. Script không ghi key vào kết quả. SDK retry tắt, provider
timeout là 12 giây và timeout mỗi lượt là 50 giây. Kết quả được lưu sau mỗi lượt.

Tiếp tục một lượt live bị ngắt:

```powershell
.\.venv\Scripts\python.exe -m evaluation.live_groq_smoke --resume
```

Chạy không có `--resume` sẽ tạo bộ kết quả mới. Latency live gồm API, LLM và tool
trong tiến trình; chưa gồm ghi âm, STT, TTS, trình duyệt hoặc thời gian đến token
đầu tiên.

`live_groq_stt_benchmark.py` đo Groq STT trên audio có transcript tham chiếu.
Nó nhận file audio bên ngoài repo để không lưu audio vào codebase, chạy 3 lượt
mặc định, ghi model thực sự trả lời, fallback trace, latency, WER và CER nếu có
transcript chuẩn. Có thể truyền transcript dài bằng `--reference-file`; timestamp
dạng `[00:00:00]` sẽ được bỏ khi tính lỗi. Dùng `--omit-transcription` khi chỉ
cần đo tốc độ mà không lưu nội dung nhận dạng vào báo cáo. Ví dụ với một clip
tiếng Việt:

```powershell
.\.venv\Scripts\python.exe -m evaluation.live_groq_stt_benchmark `
  --audio "$env:TEMP\retail_groq_stt_fleurs_vi_0000.wav" `
  --reference "văn hóa và bộ lạc cổ xưa đã bắt đầu giữ những con vật này để dễ lấy sữa tóc thịt và da" `
  --source "Google FLEURS vi_vn_0000 (CC-BY 4.0)" `
  --trials 3
```

Kết quả mẫu ngày 01/10/2026 ở `evaluation/results/live_groq_stt_benchmark.json`;
báo cáo tổng hợp nằm trong `evaluation/results/evaluation_report.md`. WER/CER
được chuẩn hóa chữ hoa/thường, dấu câu và Unicode; WER tách từ theo khoảng
trắng. Một audio lặp nhiều lượt chỉ hữu ích để đo độ ổn định latency, chưa phải
đánh giá độ chính xác đại diện.

Báo cáo sửa P0 nằm tại `evaluation/results/evaluation_report.md`.
