# Quy tắc tư vấn mặt hàng có bằng chứng

## Kết luận nghiệp vụ

Với câu hỏi về sản phẩm, agent cần xác định đúng biến thể/SKU trước, lấy dữ kiện từ catalog, rồi mới trả lời. Tên sản phẩm hoặc thương hiệu không phải bằng chứng cho giá, thành phần, công dụng, lợi ích sức khỏe hay cách dùng. Nếu catalog trả về nhiều biến thể gần giống, agent hỏi rõ vị/khối lượng/bao bì; nếu thuộc tính không có nguồn đáng tin, agent nói chưa xác minh được.

Quy tắc này phù hợp với nghiệp vụ hàng tiêu dùng đóng gói: [Codex CXS 1-1985](https://www.fao.org/4/Y2770E/y2770e02.htm) yêu cầu nhãn thực phẩm không gây hiểu nhầm và có hướng dẫn sử dụng khi cần thiết. [GS1 Global Data Model](https://www.gs1.org/standards/gs1-global-data-model-attribute-implementation-guide/13-1) biểu diễn `Consumer Usage Instructions` như một thuộc tính riêng; do đó không được suy ra hướng dẫn từ tên hàng. Nghiên cứu [Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks](https://arxiv.org/abs/2005.11401) là cơ sở cho việc gắn câu trả lời vào nguồn truy xuất được; đây là nguyên tắc kiến trúc, không phải bảo đảm rằng RAG tự loại bỏ mọi hallucination.

## Kiểm tra dữ liệu hiện có

`data/winmart_products.json` có 3.099 sản phẩm: 3.081 dòng được nhận và 18 dòng bị quarantine vì giá khuyến mãi cao hơn giá thường. Dataset có ID/barcode, tên, brand, category, giá, khuyến mãi, tồn kho và một số metadata. Trường `description` có ở các dòng nhưng sau chuẩn hóa khoảng trắng không có mô tả nào dài hơn tên sản phẩm. Dataset không có trường hướng dẫn sử dụng, công dụng, dinh dưỡng, dị ứng hoặc thành phần đáng tin cậy. PostgreSQL mapper hiện cũng để `ingredients`, `shelf` và `expiry_at` là chưa có dữ liệu xác minh.

Snapshot giá/tồn kho WinMart trong lần benchmark này được ghi ngày **15/09/2026**. Khi trả giá/tồn kho, agent nêu ngày dữ liệu để khách biết đây là snapshot; giá tại quầy có thể đã thay đổi.

## Hành vi đã thực hiện

- Giá, khuyến mãi, tồn kho và các thuộc tính có snapshot được render từ kết quả tool, không lấy số do LLM tự viết.
- `get_product_snapshot` chỉ được đọc cho sản phẩm đã xuất hiện trong kết quả tìm kiếm và khớp duy nhất với các từ định danh cụ thể. Tìm theo brand chung như “Lay's” thì hỏi vị/khối lượng thay vì tự chọn một SKU.
- Câu hỏi gộp như “giá bao nhiêu và có lợi ích gì?” được tách: trả giá có nguồn và nói rõ phần lợi ích chưa xác minh. Nội dung không có bằng chứng được bỏ khỏi prompt tra cứu của LLM.
- Thuộc tính đơn lẻ không có dữ liệu như thành phần/công dụng/cách dùng được từ chối đoán; câu trả lời hướng khách kiểm tra nhãn.
- Với một phần snapshot có sẵn và phần còn lại thiếu, tool giữ lại dữ kiện có thật để agent vẫn trả được phần đã xác minh.

## Kết quả kiểm tra

Live run ngày 18/10/2026 dùng Groq Qwen và catalog PostgreSQL WinMart:

| Tình huống | Kết quả |
|---|---|
| Teppy Minute Maid 1L: giá + lợi ích sức khỏe | Trả 24.700đ theo snapshot 15/09; không bịa lợi ích |
| Lay's Stax vị tự nhiên 110g | Chọn đúng SKU, trả 35.000đ theo snapshot 15/09 |
| Teppy: hỏi thành phần | Nói rõ chưa xác minh, không tự liệt kê thành phần |
| Lay's: chỉ nêu thương hiệu và hỏi giá | Hỏi lại vị/khối lượng, không báo nhầm giá |
| Teppy: hỏi công dụng/cách dùng | Từ chối đoán, không gọi LLM hay tool |
| Teppy: hỏi lợi ích sức khỏe | Từ chối đoán, không gọi LLM hay tool |

Tổng cộng **6/6 tình huống đạt**, 6 HTTP 200, 7 lượt gọi LLM và 4 lượt tìm catalog PostgreSQL. Median thời gian mỗi lượt là 12.473ms, có tính pacing 12 giây giữa các lần gọi để giữ quota; đây không phải latency xử lý thuần. Traces cho thấy median một lần gọi Qwen là 534ms (min 508ms, max 1.302ms). Đây là coverage test nhỏ, không phải ước lượng xác suất hallucination trong vận hành.

Report: `evaluation/results/live_vietnamese_product_consultation_final_20261018.json`
Case set: `evaluation/cases/vietnamese_product_consultation_grounded_v1.json`

## Dữ liệu cần bổ sung để agent tư vấn công dụng thật

Nạp thông tin từ nhãn/manufacturer hoặc nguồn GS1 được duyệt vào các field có cấu trúc, mỗi giá trị kèm nguồn, ngày thu thập, thị trường, phiên bản và trạng thái xác minh:

- `ingredients`, `allergens`, `nutrition` (đơn vị/khẩu phần rõ ràng).
- `consumer_usage_instructions`, `storage_instructions`, đối tượng/cảnh báo sử dụng.
- `health_claims` chỉ khi nội dung được phép công bố và nguồn có thể kiểm tra; không biến claim tiếp thị thành tư vấn y tế.

Chỉ xuất field khi có provenance hợp lệ. Nếu sản phẩm không có trường tương ứng hoặc nguồn hết hạn, tiếp tục abstain và dẫn khách xem bao bì/nhân viên. Tránh lấy văn bản product description không kiểm duyệt làm chỉ dẫn tool hoặc bằng chứng duy nhất.
