# Kiểm tra và sửa tư vấn trên toàn bộ danh mục — 31/10/2026

Thay đổi nằm ở bộ phân tích câu hỏi, truy xuất theo taxonomy và bộ dựng câu trả lời
dùng chung. Không cài câu trả lời hoặc mã sản phẩm riêng cho ví dụ của người dùng.

## Những lỗi đã sửa

- Hiểu các cách mở đầu như `t đang cần mua`, `tui muốn mua`, `trong các loại…`
  và tách nhu cầu sử dụng khỏi tên hàng để tránh truy xuất hàng chỉ trùng từ.
- Giữ dấu tiếng Việt khi tìm tên hàng; phân biệt từ trong tên hàng với từ chỉ kệ,
  vị trí và lệnh loại trừ. `Loại bơ/bò` không còn bị đọc thành `loại bỏ`.
- Ưu tiên trả lời câu hỏi vị trí dù câu có từ `mua`; bỏ lời nhắc kiểm tra nhãn
  không liên quan khi nhóm tuổi đã có bằng chứng trong taxonomy đồ dùng học sinh.
- Khi xếp hạng theo giá, tên SKU trùng tên nhóm không được làm mất các lựa chọn
  khác trong nhóm. Tìm giá thấp nhất trên phạm vi nhóm thực tế.
- Rút danh sách tư vấn trong lời nói xuống tối đa ba sản phẩm; chỉ nêu một lần
  dãy/khoang chung khi các sản phẩm cùng vị trí. Vẫn ghi rõ sơ đồ kệ là đề xuất.
- Loại bỏ câu dựng riêng cho món lẩu/cắt lát trong nhánh gợi ý thay thế; dùng
  cách chế biến và phạm vi ngành hàng của yêu cầu hiện tại.

## Lượt bán cho demo

`DEMO_SALES_ENABLED=true` chỉ hoạt động với `APP_ENV=development`. Mỗi SKU nhận
một số bán mô phỏng ổn định trong 30 ngày; không ghi vào đơn hàng hoặc doanh số thật.
Lời trả lời và thẻ sản phẩm đều có nhãn mô phỏng. Production bỏ qua cờ này.

Xếp hạng quét các trang trong phạm vi nhóm hoặc các sản phẩm vừa hiển thị, giữ
giới hạn giá, tổng tiền theo số lượng, loại trừ thương hiệu và quy cách có bằng
chứng trong tên hàng. Không tạo đánh giá khách hàng hay khẳng định công dụng.
Nguồn phân trang lặp hoặc yêu cầu thiếu dữ kiện không được tạo một người thắng giả.

## Phạm vi kiểm tra

Danh mục nguồn có **3.081 sản phẩm, 346 đường phân loại**.

| Kiểm tra | Phạm vi | Kết quả |
| --- | --- | --- |
| Ma trận danh mục | 5 mẫu × 346 đường phân loại = 1.730 ca | Qua toàn bộ |
| Hội thoại qua HTTP API | 22 nhóm theo ba cấp taxonomy × 5 lượt = 110 lượt | Qua toàn bộ |
| Backend pytest toàn bộ | 1.590 qua, 1 bỏ qua do quyền tạo symlink Windows | 292,74 giây |
| Frontend Vitest | 100 test, 9 tệp | Qua |
| Frontend TypeScript + Vite build | Bản có nhãn lượt bán mô phỏng | Qua |
| Ruff, compileall, git diff --check | Mã nguồn cuối cùng | Qua |

Năm mẫu danh mục gồm hỏi mua thân mật, hỏi vị trí, rẻ nhất, giới hạn ngân sách
và bán chạy trong demo. Oracle kiểm tra mã SKU thuộc nhóm nguồn, giá thấp nhất
ở kết quả đầu, giới hạn giá và người dẫn đầu mô phỏng trên cả nhóm.

Hội thoại kiểm tra chọn rẻ nhất trong danh sách, tham chiếu sản phẩm đầu tiên,
hỏi vị trí, đổi ngành hàng rồi hỏi giá người dẫn đầu mô phỏng. Các lượt này dùng
catalog trực tiếp, chặn lời gọi mô hình và kiểm tra không phát sinh mua hàng.

Các test bổ sung dùng taxonomy giả với ngành hàng mới để kiểm tra cú pháp chung,
phân biệt dấu, phủ định thật, lọc quy cách, ngân sách tổng, phân trang và cờ production.

Đây là ma trận hữu hạn và các bất biến kỹ thuật. Kết quả không có nghĩa đã kiểm
tra mọi câu tiếng Việt, mọi nhu cầu sử dụng hoặc chất lượng tư vấn do người dùng
đánh giá. Danh mục snapshot cũng chưa xác minh tồn kho và vị trí trưng bày thực tế.

## Chạy lại và bằng chứng

```powershell
uv run python -m scripts.evaluate_consultation_coverage
uv run pytest -q --tb=short
uv run ruff check backend scripts/evaluate_consultation_coverage.py tests/test_demo_sales.py tests/test_consultation_generalization.py
npm --prefix apps/kiosk-airi run test:run
npm --prefix apps/kiosk-airi run build
```

- [Ma trận chi tiết và các phản hồi API](artifacts/consultation_coverage_20261031.json)
- [Log quét danh mục](artifacts/consultation_coverage_run_20261031.txt)
- [Log test backend toàn bộ](artifacts/consultation_backend_gate_20261031.txt)
- [Log test tập trung](artifacts/consultation_regression_gate_20261031.txt)
- [Bản ghi kiểm tra và hash mã nguồn](artifacts/consultation_quality_gate_20261031.json)

Đã kiểm tra trực tiếp giao diện: hỏi bán chạy nồi cơm điện hiển thị 440 lượt bán
**mô phỏng**, rồi chuyển sang hỏi vị trí thước kẻ trả đúng đồ dùng học sinh, không
kéo theo sản phẩm điện gia dụng hoặc lời nhắc nhãn không liên quan.

![Demo sau khi chuyển ngành hàng](artifacts/consultation_demo_20261031.jpg)

Demo đang chạy tại `http://127.0.0.1:5173/`, API `http://127.0.0.1:8000/`.
