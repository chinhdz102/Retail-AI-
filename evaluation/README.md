# Retail evaluation

Các script này kiểm các phần đã có của `Retail_AI_Evaluation_Test_Plan.md`.
Chúng không thay thế bộ 120–180 scenario, 4 cấu hình được đề xuất trong tài liệu.

Chạy từ thư mục gốc repo:

```powershell
.\.venv\Scripts\python.exe evaluation\run_smoke_benchmark.py
.\.venv\Scripts\python.exe -m evaluation.live_groq_smoke
```

`run_smoke_benchmark.py` dùng phản hồi Groq được lập trình sẵn để kiểm backend,
grounding, phân quyền tool, session và các tình huống lỗi. Exit code 1 nghĩa là
có ca FAIL; kết quả luôn được lưu ở `evaluation/results/smoke_benchmark.json`.

`live_groq_smoke.py` chạy 9 scenario × 3 lượt bằng Groq thật. Nó mặc định đọc key
từ `.env`; có thể chọn một file cấu hình đã được cấp phép bằng biến
`RETAIL_EVAL_ENV_FILE`. Script không ghi key vào kết quả. SDK retry được tắt,
timeout provider là 12 giây và timeout mỗi lượt là 50 giây. Kết quả được lưu sau
từng lượt ở `evaluation/results/live_groq_smoke.json`.

Tiếp tục một lượt live bị ngắt:

```powershell
.\.venv\Scripts\python.exe -m evaluation.live_groq_smoke --resume
```

Chạy không có `--resume` sẽ tạo một bộ kết quả mới. Latency trong báo cáo live
là thời gian API trong tiến trình gọi LLM và tool rồi trả toàn bộ câu trả lời;
chưa gồm ghi âm, STT, TTS, trình duyệt hoặc thời gian đến token đầu tiên.

Báo cáo hiện trạng ngày 01/10/2026 nằm tại
`evaluation/results/evaluation_report.md`.
