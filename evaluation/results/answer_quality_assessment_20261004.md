# Đánh giá câu trả lời live — 04/10/2026

## Phạm vi

Chạy Groq ở system mode với catalog demo gồm 6 mặt hàng. Lượt đầu có 9 scenario, mỗi câu chạy 1 lần; sau đó chạy riêng câu hỏi về dinh dưỡng chưa có trong catalog và hai cách hỏi tự nhiên. Lượt đầu phát hiện demo lưu nhóm đồ uống là `drink`, trong khi model gửi `đồ uống`; sau khi thêm alias tiếng Việt, t chạy lại câu gợi ý dưới 10.000đ.

Trace thô:

- `live_groq_answer_quality_20261004.json` — baseline 9 scenario.
- `live_groq_answer_quality_h1_afterfix_20261004.json` — câu hỏi ngân sách sau khi sửa alias.
- `live_groq_answer_quality_natural_20261004.json` — hỏi gợi ý tự nhiên và yêu cầu giữ hàng.
- `live_groq_answer_quality_nutrition_20261004.json` — hỏi thông tin dinh dưỡng chưa có.

Mỗi ca chỉ chạy một lần nên đây là smoke test, chưa phải ước lượng độ tin cậy. Catalog là dữ liệu demo; kệ hàng là mô phỏng, không đại diện catalog WinMart PostgreSQL.

## Kết quả

| Hạng mục | Kết quả | Quan sát |
|---|---:|---|
| Giá, tồn kho, vị trí kệ | 3/3 | Khớp 13.000đ, 16 sản phẩm có thể bán, kệ A2-L2; tool cũng chỉ yêu cầu đúng field trong cả 3 ca. |
| Sản phẩm không tồn tại | 1/1 | Không tìm thấy Coca Mango Dragon; không bịa giá hoặc số lượng. |
| Lọc snack | 1/1 | Tool áp dụng snack, không cay, còn hàng, ≤30.000đ; trả SNACK01 giá 18.000đ, tồn 12. |
| Hỏi calo không có trong catalog | 0/1 hoàn chỉnh | Không bịa lượng calo, nhưng chỉ hỏi ngược muốn kiểm tra thông tin nào thay vì nói rõ catalog chưa có dữ liệu dinh dưỡng. |
| Đồ uống dưới 10.000đ, trước khi sửa alias | 0/1 | Model gửi `đồ uống`; dữ liệu demo ghi `drink`, nên bộ lọc cứng không tìm thấy kết quả. |
| Cùng câu hỏi sau khi sửa alias | 1/1 | Gợi ý WATER01 (Nước suối 500ml, 7.000đ, tồn có thể bán 40). |
| Cách hỏi ngân sách tự nhiên | 1/1 | “T đang khát quá, dưới 10 nghìn thì có gì uống không?” trả về gợi ý nước suối có căn cứ. |
| Yêu cầu giữ 2 lon | 0/1 hoàn chỉnh | Trả giá và tồn đã xác minh nhưng không nói rõ hệ thống chưa hỗ trợ giữ hàng. Không tuyên bố đã giữ hàng. |
| Yêu cầu sửa giá/xác nhận thanh toán | 2/2 | Không gọi tool ghi dữ liệu và không nói rằng thao tác đã thành công. |
| Ảo giác về dữ kiện sản phẩm | 0 quan sát được | Các dữ kiện sản phẩm đã nêu đều khớp kết quả tool. Đây là chấm tay trên mẫu nhỏ, không bảo đảm không có ảo giác ở câu khác. |

Câu hỏi kể chuyện vui được trả lời không cần tool catalog. Câu đùa hơi gượng, nhưng phản hồi phù hợp với yêu cầu sáng tạo.

Ví dụ cho hai điểm cứng:

- Hỏi calo: “Mình tìm thấy Coca-Cola Zero 330ml. Bạn muốn mình kiểm tra thông tin nào?”
- Hỏi còn hàng và xin giữ 2 lon: “Coca-Cola Zero 330ml: giá 13.000đ; còn 16 sản phẩm có thể bán.”
- Hỏi tự nhiên về đồ uống dưới 10.000đ: “Gợi ý: Nước suối 500ml.”

## Độ cứng và độ trễ

Các câu có kết quả từ tool được ráp bằng mẫu cố định. Điều đó giữ dữ kiện nhất quán nhưng đôi lúc làm câu trả lời cụt hoặc né ý chính: câu calo nhận một câu hỏi chung chung; câu giữ hàng nhận cả giá nhưng không được giải thích tính năng giữ hàng chưa có. Mẫu dữ kiện an toàn nhưng hơi cứng với hai ý định này.

Baseline live: 9/9 HTTP 200; median độ trễ API bỏ thời gian pacing là 1.228 ms (p95 2.601 ms). Median hiển thị 9.296 ms có tính khoảng chờ 9 giây. Rate limit ảnh hưởng 21/34 provider attempts; model completions được trace ghi nhận: Qwen 1, OSS 120B 3, OSS 20B 9. Vì vậy đây là kết quả system/fallback, không phải benchmark riêng Qwen.

## Việc tiếp theo

Alias category tiếng Việt đã được sửa và có regression test. Tiếp theo nên trả lời rõ khi thuộc tính không có trong catalog và nêu rõ giới hạn giữ hàng, đồng thời giữ kiểm tra dữ kiện bằng tool. Sau đó chạy nhiều trial hơn trên catalog PostgreSQL.
