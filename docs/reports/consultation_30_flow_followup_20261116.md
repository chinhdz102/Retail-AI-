# Báo cáo bộ 30 câu và flow dài

Ngày: **16/11/2026**  
Catalog giữ nguyên: `data/winmart_products.json`, SHA-256 `a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`

## Bộ kiểm thử

Đã thêm [audit_consultation_30.py](../../evaluation/audit_consultation_30.py), gồm 30 câu tiếng Việt theo các nhóm: giá thấp nhất/cao nhất, khoảng giá, so sánh, chọn một sản phẩm, ngân sách, phủ định, thay thế, quy cách, vị trí và câu mơ hồ. Flow dài có **18 lượt**, gồm bữa tối → đổi ngân sách → giữ món đã có → chuyển đồ uống → đổi quy cách → chuyển dầu gội → quay lại nhắc lựa chọn.

Artifact:

- [consultation_30_and_long_live_20261115.json](artifacts/consultation_30_and_long_live_20261115.json): lần chạy 30 câu + 18 lượt khi pool provider bị giới hạn phần lớn lượt.
- [consultation_long_flow_live_r2_20261116.json](artifacts/consultation_long_flow_live_r2_20261116.json): flow dài chạy lại sau khi quota hồi phục một phần.
- [consultation_price_extrema_live_r2_20261115.json](artifacts/consultation_price_extrema_live_r2_20261115.json): HTTP thật cho bốn câu cực trị giá.

## Sửa trong đợt này

- Câu “thấp nhất/cao nhất/rẻ nhất/đắt nhất” và “thấp đến cao” được tách khỏi tên sản phẩm. Backend lấy toàn bộ nhóm hàng, sắp xếp giá bằng code và chỉ render sản phẩm cực trị đã xác minh.
- Bỏ các từ chỉ thứ tự/phạm vi như “hiện tại”, “loại”, “khoảng”, “mức” khỏi query giá để không làm rỗng kết quả.
- Câu “nhà có dầu ăn/gia vị, không tính thêm” giữ consultation context thay vì rơi vào lookup giá.
- Draft câu hỏi không được hỏi số lượng/khẩu phần khi mới biết nhóm đồ uống; phải xác định lựa chọn trước.
- Khi khách yêu cầu “không quảng cáo, nói một điểm cần cân nhắc”, draft không được biến thành câu hỏi thêm thành phần.
- No-match bị reviewer bác cũng có đường hạ xuống guidance có kiểm soát trong lượt còn lại.
- Câu không xác định được món được đổi thành “chưa xác định được loại hàng…” để nói như nhân viên, không đọc “danh mục hiện có”.

## Kết quả

- Giá cực trị HTTP thật: **4/4 đạt** sau sửa: nước suối thấp/cao, dầu gội rẻ nhất và thịt thấp–cao; đều trả số tiền, SKU và `model=catalog` trong khoảng 0,05–0,27 giây.
- Bộ test giá: **34 passed**.
- Consultation và policy liên quan: **276 passed**.
- Toàn bộ backend với `CATALOG_REPOSITORY=demo`: **917 passed** sau khi thêm
  regression cho provider cooldown; XML trước đó ở
  [backend_consultation_30_20261116.xml](artifacts/backend_consultation_30_20261116.xml).
- Flow 18 lượt: **12/18 HTTP 200**, **6/18 HTTP 502 assistant_unavailable**. Sáu lỗi này là các draft/review không vượt guard trong flow (vượt ngân sách, thiếu món bắt buộc hoặc không chốt được biến thể), không được tính là tư vấn đạt. Lần chạy này không ghi nhận `assistant_busy` ở các lượt flow.

Các lượt đạt cho thấy state đã giữ thịt heo 300g, loại rau, ngân sách mới và tách chủ đề đồ uống/dầu gội. Tuy nhiên còn hai nhóm cần làm tiếp:

1. Khi một draft basket vượt ngân sách, model đôi lúc không tự thay bằng ứng viên rẻ hơn trước khi hết ba lượt sửa.
2. Khi không có đúng trà không đường/không ga 1 lít dưới ngân sách, một số lượt hỏi lại hoặc trả guidance, nhưng các lượt chốt biến thể vẫn có thể hết lượt review.

Không dùng `HTTP 200` hay số test unit để gọi là chất lượng nhân viên thật; chất lượng nội dung vẫn cần chấm người trên holdout, nhất là các flow dài.

