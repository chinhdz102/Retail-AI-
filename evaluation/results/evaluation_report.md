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

## Giới hạn xác minh

Benchmark live không được chạy lại sau thay đổi: trong phiên này repo không có
`.env` và `GROQ_API_KEY` không được nạp vào môi trường. Vì vậy 40/40 và smoke
23/0 là kết quả kiểm backend với phản hồi Groq mô phỏng; chúng không chứng minh
Qwen luôn chọn đúng tool hoặc `fields`. File
`live_groq_smoke.json` là kết quả trước khi đổi tool contract, không phải xác
nhận cho bản sửa này. Có thể chạy live bằng cách đặt key vào `.env` rồi dùng
lệnh trong `evaluation/README.md`.

Các mục còn ngoài sprint theo roadmap vẫn chưa được hỗ trợ: batch/FEFO, giỏ
hàng và checkout, kiểm chứng thanh toán thật, Redis, RAG và voice turn-taking.
Không mở rộng các phần này trước khi kiểm chứng tool selection live.
