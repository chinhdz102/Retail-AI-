# Báo cáo tiếp: state/policy và kiểm tra hội thoại

Ngày: 15/11/2026  
Nhánh: `codex/vietnamese-whisper-stt`  
Catalog: `data/winmart_products.json` cũ, SHA-256 `a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`

## Đã sửa

- Khi reviewer bác một câu hỏi làm rõ, Sky giữ hành động hỏi. Không được lấy SKU cũ chỉ vì phiên có sản phẩm trước đó.
- Chỉ tiếp tục dùng lựa chọn cũ khi reviewer trả rõ `repair_action=revise`, phiên chưa đổi chủ đề và search scope cũ còn hợp lệ.
- Khi khách đổi chủ đề, lịch sử evidence, SKU cũ và ràng buộc quy cách của chủ đề trước bị loại khỏi phạm vi tìm kiếm mới.
- `recommend` giới hạn `choices.maxItems=1` ngay trong schema draft; lỗi “chọn một” không còn chờ đến vòng render mới phát hiện.
- Nhận diện tiền trong lý do đã bao phủ `VNĐ`, `vnđ`, `VND`, `đ`, `₫`; code chặn lặp giá trong reason.
- Yêu cầu quà mở chưa có sở thích/kiêng kỵ bị policy ép hỏi một câu trước, kể cả khi model nhỏ bỏ qua search.
- Draft no-match bị lỗi render được chuyển sang guidance có kiểm soát trong lượt còn lại, thay vì lặp boilerplate catalog rồi trả 502.

Không thêm SKU, không hardcode quà/thịt/nước cụ thể và không thay catalog để làm test qua.

## Kiểm thử

- Regression policy/consultation: **239 passed**.
- Toàn bộ backend với `CATALOG_REPOSITORY=demo`: **903 passed**.
- Artifact test XML: [backend_dialogue_policy_20261115_r2.xml](artifacts/backend_dialogue_policy_20261115_r2.xml).
- Artifact trace HTTP trước khi sửa schema/policy: [dialogue_policy_live_20261115.json](artifacts/dialogue_policy_live_20261115.json).
- Trace trực tiếp model sau sửa cho thấy các key đều gặp `rate_limit_exceeded`; artifact giữ trạng thái lỗi nhưng không ghi credential/provider body: [dialogue_policy_gift_trace_r2_20261115.json](artifacts/dialogue_policy_gift_trace_r2_20261115.json).

## Đánh giá thực tế

Luồng đổi chủ đề đã được kiểm tra bằng HTTP: từ dầu gội sang đồ uống, ngân sách mới và loại nước có ga bị áp dụng, không giữ sản phẩm cũ. Ca quà mở ở lượt đầu hỏi sở thích đúng mục tiêu. Một lượt sau vẫn gặp 502 vì quota và một draft model lặp lý do; đây là lý do chưa thể tuyên bố tư vấn đã đạt mức nhân viên thật. Cơ chế fallback đã thử các key/model cấu hình nhưng khi toàn bộ pool bị giới hạn thì phải trả lỗi có kiểm soát.

## Việc còn lại

1. Chạy lại hai flow HTTP sau khi quota provider hồi phục, chấm từng lượt theo intent, giữ yêu cầu, grounding, tiến triển quyết định và tự nhiên.
2. Tách đường hỏi giá/tra vị trí khỏi consultation LLM để giảm số lần gọi và giảm quota.
3. Bổ sung holdout tiếng Việt theo ngành hàng và chấm người thật; không dùng số test unit để khẳng định chất lượng lời tư vấn.

