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

Báo cáo sửa P0 nằm tại `evaluation/results/evaluation_report.md`.
