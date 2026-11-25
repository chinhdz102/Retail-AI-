# Kế hoạch sửa tư vấn và dữ liệu bán hàng — 02/11/2026

## Mục tiêu và tiêu chí kiểm tra

- Lưu số lượng bán **mô phỏng** theo sản phẩm trong SQLite cho bản đồ án đang dùng catalog JSON; mỗi số nguyên từ 0 đến 999, kỳ 30 ngày. Seed lặp lại không ghi đè số đã có. API xếp hạng theo dữ liệu đã lưu. Theo yêu cầu mới của người dùng, giao diện hiện **Đã bán: N / 30 ngày**, không chèn cụm “(số liệu mô phỏng)” vào lời khách đọc; nguồn nội bộ vẫn giữ `SIMULATED`.
- Demo bật đúng luồng tư vấn LLM. Không tra toàn bộ câu giao tiếp như tên hàng; giữ mục đích và ràng buộc khi khách nói “tư vấn cho t với”, sửa câu trả lời hoặc đổi ngân sách.
- Cho phép kiến thức mua sắm/nấu ăn thông thường để nối nhu cầu với nhóm hàng. Tên, giá, số lượng, tổng và ID vẫn được kiểm tra bằng catalog. Không chuyển tên mỹ phẩm thành cam kết điều trị hay đoán an toàn cho da/thú cưng.
- LLM thực sự viết lời khuyên gắn với nhu cầu, thay vì chỉ chọn ID rồi bị thay bằng một mẫu liệt kê cố định. Mỗi lượt có hướng chọn/lý do hoặc một câu hỏi thiết yếu; không ghép các cảnh báo kỹ thuật vào lời nói.
- Kiểm tra các ảnh/câu người dùng đưa, hội thoại nhiều lượt và các ngành hàng khác. Tách test xác định khỏi kiểm tra provider thật; ghi rõ quota, lỗi và đáp án chưa đạt.

## Công việc

1. Rà cấu hình đang chạy, tuyến API, bộ nhớ, truy xuất, renderer và nơi lưu catalog. **Đã rà:** hai cờ tư vấn/bán mô phỏng đang tắt; renderer lựa chọn dùng mẫu cố định; thiếu usage bị coi như thiếu mọi cơ sở tư vấn.
2. Đọc nguồn gốc về query rewrite, lập mục tiêu hội thoại, ProductAgent và hướng dẫn dữ liệu/da mụn; ghi nguồn và điều áp dụng, không gán điểm paper cho dự án.
3. Thêm DB, seed, xếp hạng và thẻ bán hàng. Test giới hạn, lưu bền, seed idempotent, đổi số ảnh hưởng kết quả, ràng buộc ngân sách/loại trừ và không dùng khi tắt demo.
4. Sửa planner/retrieval/realization và giữ ngữ cảnh; test lỗi lặp, sai nhóm, giá bịa và công dụng điều trị.
5. Restart demo với cấu hình lưu lại, test API thật và UI. Chạy bộ backend/frontend và build phù hợp; lập báo cáo gồm đáp án thực tế, số test và hạn chế.

Không sửa/đánh giá lại STT/TTS/VAD trong đợt này vì các lỗi mới được gửi là dữ liệu bán và tư vấn chữ.

## Cập nhật triển khai

- Đã thực hiện 1–4: DB, seed, native structured output, ngữ cảnh tư vấn, placeholder tên/giá và kiểm tra từng món được chọn. Thêm kho hướng dẫn chung có nguồn AAD/CDC/WHO/ASPCA; không có bảng câu hỏi → đáp án hay ID hàng theo ví dụ.
- Đã kiểm tra nhãn bán trên UI thật và lưu ảnh. Đang hoàn tất bước 5 bằng bộ test toàn dự án và replay đủ 35 câu cùng các hội thoại nhiều lượt; báo cáo kết quả sẽ tách điểm kỹ thuật khỏi đánh giá nội dung.
