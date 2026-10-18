# Đánh giá hội thoại text và grounding — 13/10/2026

## Phạm vi

- Agent chạy với `CATALOG_REPOSITORY=demo`, gồm 6 sản phẩm trong `backend/products/catalog.py`.
- `data/winmart_products.json` có 3.099 dòng; dry-run kiểm tra schema nhận 3.081 dòng và cách ly 18 dòng vì giá khuyến mãi cao hơn giá gốc.
- PostgreSQL không lắng nghe tại `127.0.0.1:5432`, nên chưa thể đo retrieval hoặc trả lời Groq trên 3.081 dòng WinMart. Kiểm tra dữ liệu offline không được tính là kiểm tra khả năng trả lời của agent.
- Chỉ kiểm tra text. STT, TTS và đồng bộ khẩu hình không thuộc benchmark này.

## Bài học từ paper và thay đổi trong code

1. **Chấm riêng truy xuất và trả lời.** RAGAS tách độ liên quan của context, faithfulness và answer relevance; benchmark mới cũng kiểm tra riêng product ID, field được hỏi, bộ lọc người dùng nêu và nội dung bị cấm. Một câu trả lời nghe trôi chảy không đủ chứng minh nó đúng. [RAGAS](https://arxiv.org/abs/2309.15217)
2. **Kiểm tra từng claim, kể cả khi đã có retrieval.** RAGTruth ghi nhãn lỗi ở mức câu trả lời và span từ, đồng thời cho thấy RAG vẫn có thể phát sinh claim không được evidence hỗ trợ. Với catalog có schema, API hiện render dữ kiện trực tiếp từ snapshot; thuộc tính ngoài schema như calo bị trả lời là chưa có dữ liệu. [RAGTruth](https://aclanthology.org/2024.acl-long.585/)
3. **Không coi retrieval là luôn đúng.** CRAG đánh giá chất lượng retrieval rồi chọn hành động phù hợp khi evidence yếu. Ở codebase này, cách tương ứng là dùng snapshot đã xác minh, giới hạn claim vào field được hỏi, và từ chối khi không có dữ liệu. Đây là áp dụng nguyên tắc vào catalog dạng bảng; chưa triển khai mô hình CRAG đầy đủ. [CRAG](https://arxiv.org/abs/2401.15884)
4. **Chỉ lấy thêm evidence khi câu hỏi cần.** Self-RAG dùng retrieval thích ứng và bước tự phản biện. Thay đổi ở đây dùng cơ chế nhẹ, xác định được: nếu câu hỏi ngắn như “giá bao nhiêu?” nối tiếp một sản phẩm duy nhất đã xác minh, backend gọi snapshot đúng sản phẩm trước khi trả lời. Nếu có nhiều sản phẩm khả dĩ, không tự gán ngữ cảnh. [Self-RAG](https://arxiv.org/abs/2310.11511)

Các thay đổi chính:

- Session nhớ ID của một sản phẩm đã được xác minh; câu hỏi nối tiếp lấy snapshot mới và chỉ render field khách hỏi.
- Bộ lọc danh mục được giữ như điều kiện cứng. Nếu khách nói “đồ ăn vặt” nhưng model bỏ `category`, backend bổ sung `snack` trước khi chạy recommendation.
- Snapshot hỗ trợ field `spicy`; câu hỏi dinh dưỡng không có trong catalog được từ chối rõ ràng.
- Yêu cầu sửa giá/tồn kho hoặc xác nhận thanh toán luôn nhận lời từ chối cố định, không phụ thuộc cách model diễn đạt.
- Câu trả lời factual dùng mẫu ngắn, thân thiện; khi khách chỉ nêu tên mặt hàng, agent hỏi muốn xem giá, tình trạng hàng hay vị trí kệ thay vì tự đọc toàn bộ thuộc tính.

## Kết quả

- Baseline Groq trước các sửa đổi: 12 tình huống, 14 lượt, 14/14 HTTP 200. Bộ chấm ban đầu báo 11/14 đạt: hai lượt nối tiếp không khôi phục sản phẩm; một ca từ chối sửa giá an toàn nhưng grader yêu cầu đúng một cách diễn đạt. Đồng thời phát hiện một vấn đề mà grader ban đầu bỏ sót: gợi ý “đồ ăn vặt” có lẫn Coca Zero và nước suối.
- Regression live chọn lọc sau sửa có 7 lượt: 6/7 đạt grader lần đầu. Lượt còn lại là câu từ chối sửa giá an toàn nhưng khác chính tả với chuỗi grader bắt buộc; sau đó thêm từ chối xác định và chạy lại ca này, đạt 1/1.
- Các lượt post-fix xác nhận snack chỉ trả `SNACK01` (không còn đồ uống lẫn vào); mì cay được xác minh qua field `spicy`; câu hỏi dinh dưỡng trả lời chưa có dữ liệu; hội thoại ba lượt nhớ đúng Coca Zero và trả giá 13.000đ, sau đó đúng kệ A2-L2.
- Regression local: 91/91 pytest, 23/23 smoke, 40/40 P0 regression; Ruff sạch.
- Báo cáo live đầy đủ, lượt regression chọn lọc, ca sửa giá và kiểm tra dữ liệu WinMart được lưu cạnh file này dưới dạng JSON.

## Chấm độ tự nhiên và giới hạn

Các câu trả lời sau được đọc lại theo tiêu chí trực tiếp, ngắn gọn, đúng ngữ cảnh và không thêm claim chưa kiểm chứng:

- “Giá Coca-Cola Zero 330ml là 13.000đ nhé.”
- “Coca-Cola Zero 330ml ở kệ A2-L2 nhé.”
- “Snack khoai tây vị tự nhiên không cay nhé.”
- “Mình chưa có dữ liệu đã xác minh cho thông tin đó. Bạn kiểm tra giúp mình trên nhãn sản phẩm nhé.”

Độ tự nhiên được chấm định tính; benchmark một lượt cho mỗi prompt chưa ước lượng được tỷ lệ hallucination của hệ thống. Cụm “40 sản phẩm” vẫn hơi máy móc vì các dòng demo chưa khai báo đơn vị bán; nên bổ sung `uom` trước khi tối ưu câu tồn kho theo “chai/lon/gói”. Demo vẫn có animation `talk` chung; chưa có lip-sync theo âm vị.
