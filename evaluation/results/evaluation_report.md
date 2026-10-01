# Kết quả triển khai P0 — 01/10/2026

Đã áp dụng sprint sửa lỗi theo roadmap nghiên cứu. Thay đổi tập trung vào tool
catalog, grounding và tính đúng đắn của các fact/action nhạy cảm.

## Kết quả sau sửa

| Kiểm tra | Kết quả | Phạm vi |
|---|---:|---|
| Backend pytest | 46/46 PASS | API, tool contract, provenance, timeout và catalog |
| Ruff file thay đổi | PASS | Backend, evaluation scripts và tests liên quan |
| P0 regression | 40/40 PASS | 8 lỗi × 5 lượt, Groq được mô phỏng |
| Smoke benchmark | 23 PASS, 0 FAIL, 7 UNSUPPORTED | Backend thật, model được lập trình sẵn |
| Groq live chat | 27/27 HTTP 200 | 9 tình huống × 3 lượt, chạy sau bản sửa |
| Groq Whisper STT | 3/3 chính xác | Một audio FLEURS tiếng Việt dài 11,34 giây, lặp 3 lượt |

Kết quả chi tiết:
[p0_regression_5x.json](p0_regression_5x.json),
[smoke_benchmark.json](smoke_benchmark.json).

Ruff toàn repo vẫn báo 4 lỗi có sẵn trong `crawl_winmart.py`; file crawler không
thuộc phạm vi P0 và không được sửa trong sprint này.

## Thay đổi chính

- Bỏ `_fact_intent` và validator kiểm số/chuỗi con. Tool `get_product_snapshot`
  yêu cầu model khai báo `fields`; backend render fact trực tiếp từ snapshot và
  gắn đúng tên sản phẩm lấy từ catalog.
- Tách `search_products` thành tìm ứng viên. Kết quả tool chỉ đưa ID, tên, nhóm
  hàng và bộ lọc khớp; không dùng search để trả giá/tồn/kệ nếu chưa có snapshot.
- Chặn claim fact có giá trị/đơn vị/kệ/ngày hết hạn nếu chưa có snapshot xác minh
  field đó; phản hồi search-only chỉ nêu ứng viên và hỏi khách muốn tra cứu gì.
- Bọc tool result bằng `tool`, `status`, `source`, `timestamp` và
  `provenance_id`. Claim sửa giá/tồn hoặc xác nhận PAID bị thay bằng câu chuyển
  cho nhân viên nếu không có provenance thành công tương ứng.
- Đưa thực thi tool vào thread có timeout 2 giây, retry timeout tối đa một lần;
  lỗi cuối trả thông báo an toàn thay HTTP 500.
- Thêm `sellable_stock`, loại hàng quá hạn khỏi tìm kiếm `in_stock`, và chỉ
  trả số lượng có thể bán cho agent. Fixture sữa hết hạn còn 24 đơn vị vật lý
  nhưng số lượng có thể bán là 0.
- Ghi fallback trace của từng model trong kết quả chat/transcription, log loại
  lỗi đã lọc, đưa trace vào live evaluation và trả đúng model STT đã phản hồi.

## Benchmark Groq live — 01/10/2026

| Đo lường | Kết quả |
|---|---:|
| Agent API | 27/27 HTTP 200; median 4.556 giây, p95 10.794 giây, trung bình 5.293 giây |
| Lượt gọi LLM | 60; median 1.885 giây, p95 6.039 giây, trung bình 2.380 giây |
| Model trả lời | Qwen3.8-27B: 20; GPT-OSS-120B: 35; GPT-OSS-20B: 5 |
| Whisper STT | `whisper-large-v3-turbo`: median 3.875 giây, p95 4.645 giây |
| STT trên audio mẫu | WER 0, CER 0 trong cả 3 lượt |
| Audio M4A người dùng cung cấp | 47,69 giây; 3 lượt, median 15,285 giây, p95 20,394 giây |
| Audio MP3 người dùng cung cấp | 25:07, 1 lượt 142,616 giây (~10,57× thời lượng audio) |

Chat fallback đã được kích hoạt thật: Qwen gặp 39 rate limit và 1 timeout; GPT-OSS-120B gặp thêm 5 rate limit. Các lần đó được xử lý qua model kế tiếp, và cả 27 phản hồi agent đều thành công. Các số lần lỗi là số lần thử model, không phải số hội thoại. Benchmark ghi chi tiết từng lượt và model thực sự trả lời trong [live_groq_smoke.json](live_groq_smoke.json).

STT dùng một clip đọc tiếng Việt dài 11,34 giây từ [Google FLEURS](https://huggingface.co/datasets/google/fleurs), lấy từ bản [FluidInference/fleurs-full](https://huggingface.co/datasets/FluidInference/fleurs-full/tree/main/vi_vn) tại commit `f70cb0b`, giấy phép CC-BY 4.0. Turbo trả đúng transcript tham chiếu ở cả 3 lần; model fallback không cần dùng. Đây là một clip đọc có transcript, không đại diện cho độ chính xác trên giọng nói hội thoại ở quầy bán lẻ. Kết quả và WER/CER nằm trong [live_groq_stt_benchmark.json](live_groq_stt_benchmark.json).

Hai file audio người dùng gửi cũng được thử bằng turbo và đều thành công, không gọi model fallback. M4A được gửi trực tiếp. MP3 gốc 29,26 MB vượt giới hạn upload 25 MB của free tier, nên được chuyển tạm thành MP3 mono 16 kHz/48 kbps, 9,05 MB trước khi gửi; file gốc không đổi. Không có transcript tham chiếu cho hai file này nên không chấm WER/CER. Latency MP3 chỉ có một lượt và không tính thời gian chuyển đổi; bản nén có thể ảnh hưởng nhẹ đến chất lượng nhận dạng. Audio và transcript từ các file này không được lưu trong repo.

Latency tính từ lúc gửi request qua SDK đến lúc nhận câu trả lời, gồm mạng và thời gian xử lý của Groq; chưa gồm ghi âm, trình duyệt hoặc TTS. Các số liệu này là một smoke benchmark nhỏ, không thay thế kiểm tra tải hoặc tập đánh giá nhiều người nói.

Benchmark live bổ sung cho các kết quả giả lập bên trên; 40/40 regression và smoke 23/0 vẫn không chứng minh Qwen luôn tự chọn đúng tool hoặc `fields`. Key được nạp từ `.env` đã bị Git bỏ qua; `.env.example` chỉ giữ placeholder và không đưa credential vào kết quả.

Các mục còn ngoài sprint theo roadmap vẫn chưa được hỗ trợ: batch/FEFO, giỏ
hàng và checkout, kiểm chứng thanh toán thật, Redis, RAG và voice turn-taking.
Không mở rộng các phần này trước khi kiểm chứng tool selection live.

## Kiểm tra caption người dùng — 01/10/2026

| Audio | Phạm vi đối chiếu | Kết quả |
|---|---|---|
| MP3 review 7-Eleven | 22:14.09 đầu tiên trong audio 25:07.57; clip gửi API 8,004,788 byte | `whisper-large-v3-turbo`, 3/3 thành công, không fallback; latency 4,713 / 5,742 / 6,965 ms, median 5,742 ms, p95 6,965 ms |
| M4A cửa hàng tiện lợi | Caption người dùng dừng ở 00:32, audio dài 47.69 giây | Không chấm WER/CER vì caption chỉ bao phủ một phần và là văn bản biên tập |

So sánh literal MP3 cho ra WER 3.748–3.751 và CER 3.264–3.266. Không dùng các số này làm điểm chính xác: caption là bản tóm lược, chỉ tới 22:14, trong khi nhận diện trả về lời nói chi tiết (5,079 từ sau chuẩn hóa so với 1,195 từ tham chiếu). Chênh lệch phản ánh chủ yếu việc hai văn bản không cùng dạng tham chiếu nguyên văn; không thể diễn giải WER trên 100% như tỷ lệ nhận diện sai của mô hình.

Một lượt trước trên MP3 đầy đủ mất 142,616 ms; ba lượt trên clip 22:14 mất 4,713–6,965 ms. Độ chênh lớn cho thấy latency trên audio dài biến động mạnh, nên median của clip chỉ là smoke benchmark, chưa phải cam kết tốc độ ổn định. File audio và nội dung transcript không được lưu trong repo.