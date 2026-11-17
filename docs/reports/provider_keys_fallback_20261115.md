# Kiểm tra key Groq và fallback — 15/11/2026

## Kết quả kiểm tra key

Không ghi khóa thật vào báo cáo. Bốn key đã có trong cấu hình và key mới ở
fallback slot 4 đều gọi thành công tại thời điểm kiểm tra. Mỗi slot được thử
với ba model: `qwen/qwen3.8-27b`, `openai/gpt-oss-120b` và
`openai/gpt-oss-20b`; tất cả trả HTTP 200. Header trả về giới hạn 1.000
request và 8.000 token cho lượt kiểm tra, không có key nào trả 401/403.

Kết quả máy:

- 5 credential slot có cấu hình.
- 5/5 slot × 3 model thành công.
- Key mới được lưu ở `GROQ_API_KEY_FALLBACK_4` trong `.env` và backend đã
  được khởi động lại để nạp key.

## Cơ chế fallback hiện tại

`GroqService` duyệt theo thứ tự model rồi key:

1. Model chính `qwen/qwen3.8-27b` trên từng key.
2. Model dự phòng `openai/gpt-oss-120b` trên từng key nếu model chính không
   hoàn tất.
3. Các request bị 429 được ghi cooldown riêng theo cặp `(model, key slot)`;
   request kế tiếp bỏ qua cặp đang chờ và thử key/model khác.
4. Lỗi 401/403 khóa slot đó trong lượt hiện tại, không thử lại cùng key với
   các model còn lại.
5. Header giới hạn và `retry-after` chỉ lưu dưới dạng chẩn đoán; không ghi
   raw body của provider hoặc khóa vào log.

Model `openai/gpt-oss-20b` vẫn có trong cấu hình và đã kiểm tra thành công,
nhưng luồng tư vấn hiện chỉ bật 120B làm fallback để giữ chất lượng. Có thể
thêm 20B làm tầng cuối nếu ưu tiên luôn có phản hồi hơn chất lượng tư vấn;
chưa bật mặc định vì các audit trước cho thấy model này hay mất phủ định,
nhận diện sản phẩm và ranh giới tư vấn.

## Lỗi đã tái hiện và đã sửa

Thông báo trong ảnh không chỉ do rate limit. Khi model tạo lựa chọn thịt heo
bán theo kg, reviewer đã coi `300g = 59.970đ` là sai vì chỉ nhìn giá/kg.
Đã thêm `verified_purchase_lines` do code tính (đơn giá/kg, khối lượng mua,
thành tiền) vào payload reviewer. Reviewer giờ phân biệt đơn giá/kg với lượng
khách mua, không chặn phép tính hợp lệ.

Đã thêm fallback trace có `credential_slot`, trạng thái `cooldown` và số giây
còn lại. Test kiểm chứng key mới được dùng khi bốn slot trước đang cooldown,
và model 120B được dùng khi cả năm slot của Qwen đang cooldown.

## Kiểm tra thực tế sau khi sửa

Backend localhost đã được khởi động lại. Hai câu:

- “Mình muốn mua thịt heo để ăn lẩu.”
- “Ờ thì thịt heo đi.”

đều trả HTTP 200, model Qwen, mã hàng từ catalog và tư vấn tiếng Việt. Thời
gian lần lượt khoảng 4,8 giây và 2,2 giây; gồm planner, composer và reviewer.
Kết quả không còn thông báo `Consultation temporarily unavailable`.

Một lần chạy trước đó đã gặp 429 ở Qwen slot 0 và tự chuyển sang Qwen slot 1
trong chính lượt sửa câu trả lời; đây là bằng chứng fallback key hoạt động khi
provider thực sự trả rate limit.

## Kiểm thử

- Groq fallback và tư vấn: 84/84 test.
- Retail API + các test liên quan với `CATALOG_REPOSITORY=demo`: 277/277.
- Probe provider: 15/15 cặp slot/model HTTP 200.
- Probe có kiểm soát cooldown: key slot 4 nhận khi slot 0–3 bị cooldown;
  model 120B nhận khi cả 5 slot Qwen bị cooldown.

Các test retail dùng catalog demo để cô lập fixture; backend demo đang chạy
vẫn giữ catalog snapshot theo cấu hình người dùng.
