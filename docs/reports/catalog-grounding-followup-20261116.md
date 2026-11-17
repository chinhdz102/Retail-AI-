# Catalog-grounded retrieval follow-up — 2026-11-16

## Mục tiêu

Giữ tư vấn bám catalog khi planner nhận một câu tự nhiên có cả loại hàng và
mục đích sử dụng, ví dụ “thịt ăn lẩu” hoặc “rau cho bữa tối”. Từ mô tả cách dùng
không được tạo ra SKU, thương hiệu, giá, quy cách hay công dụng mới.

## Thay đổi

- Lưu tập kết quả thô trước khi kiểm tra identity.
- Khi query không khớp vì chứa cụm dùng/món, thử các cụm từ liên tiếp có trong
  chính query và tra lại catalog.
- Chỉ chấp nhận một từ backoff khi các SKU đầu có một taxonomy category chiếm đa
  số; trường hợp từ quá mơ hồ thì giữ trạng thái thiếu kết quả để planner hỏi lại.
- Giữ nguyên bộ lọc brand, size, giá, tồn và exclusion; không nới subtype mà
  khách đã nêu rõ.

## Kiểm thử

- `pytest -q tests/test_consultation.py tests/test_consultation_customer_review.py tests/test_consultation_policy.py`
  → **232 passed**.
- Sau khi rà lại guard claim, mở rộng bộ regression review/policy:
  `pytest -q tests/test_consultation.py tests/test_consultation_customer_review.py
  tests/test_consultation_policy.py tests/test_consultation_review.py`
  → **245 passed**.
- Sửa hai false positive trong guard: câu hỏi tuổi của khách không bị đọc thành
  claim “tươi”, và gợi ý chung “chọn chai nhỏ dễ mang theo/ưu tiên mùi thơm nhẹ”
  không bị chặn khi chưa chọn SKU. Khi đã có marker SKU, các claim cùng nhóm
  vẫn phải có evidence trên nhãn.
- Retrieval repair chỉ chạy khi adapter có tập sản phẩm thật để làm evidence;
  adapter/mock chỉ có `search()` không bị tiêu thêm một lượt model rồi rơi vào
  `StopIteration`/502.
- HTTP thật với snapshot catalog:
  - `Các loại thịt ăn lẩu` → các SKU category `Thịt`, không lấy bánh bao/snack/cháo.
  - `Mua thịt về ăn lẩu` → SKU có thật trong catalog, giá và product id do code render.
  - `Mua rau cho bữa tối` → các SKU có thật trong catalog, category `Rau Lá`.
  - `Mua nước uống` → hỏi lại loại nước vì catalog có nhiều nhóm nước khác nhau.

## Giới hạn còn theo dõi

Provider composition/review vẫn là phần tốn thời gian nhất của luồng tư vấn.
Các bài test toàn bộ repository hiện còn một số lỗi cũ ở nhánh tiếng Anh/tool
test-double và không liên quan đến lớp backoff catalog; nhóm consultation và
retrieval đã pass riêng như trên.

Log provider cũng cho thấy một nút thắt riêng: lỗi kết nối lặp lại trên cùng
credential làm lượt Plan thử lại cùng route trước khi chuyển model. Đã thêm
cooldown ngắn 8 giây cho `APIConnectionError`/`APITimeoutError`, tách với
`retry-after` của rate limit; lượt kế tiếp sẽ bỏ route vừa hỏng và dùng key/model
khác sớm hơn. Regression provider: `21 passed`.

Smoke live sau khi nạp lại backend:

- `Giá thấp nhất của dầu gội là bao nhiêu?` đi qua `model=catalog`, trả đúng
  SKU `TLM_10601171` và giá 80.000đ.
- `Mua bánh bao nhân thịt` trả SKU thật `TLM_10007184`, không tự đổi sang thịt
  lẩu; lượt đo này mất khoảng 14,2 giây do Plan/Composition/Review của provider.
- `Các loại thịt ăn lẩu` vẫn có thể rơi vào `catalog-fallback` khi provider trả
  draft claim không có nhãn chứng minh. Fallback giữ đúng SKU catalog và không
  bịa giá/công dụng; câu fallback đã được chỉnh tự nhiên hơn nhưng vẫn là đường
  dự phòng, chưa thay thế được giải thích phong phú của model. Đây là điểm cần
  tiếp tục giảm lỗi draft/review ở provider nếu muốn đạt mức nhân viên hoàn toàn.
