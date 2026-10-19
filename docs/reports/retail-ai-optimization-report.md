# Báo cáo hoàn thiện và kiểm thử Retail AI

- **Ngày:** 19/10/2026
- **Phạm vi:** kiosk khách hàng, session, tra cứu catalog và luồng giỏ hàng/checkout sandbox
- **Môi trường kiểm thử:** catalog demo; không thực hiện giao dịch tiền thật

## Tóm tắt

Đã hoàn thiện luồng mua thử theo session từ thêm sản phẩm đến kiểm tra trạng thái thanh toán, bổ sung bộ nhớ session có cấu trúc, giao diện giỏ hàng và xác nhận checkout, đồng thời sửa intent parser để câu hỏi “bán đắt hàng nhất” không bị hiểu nhầm thành lệnh đặt hàng.

Kiểm tra cuối: **468 test backend**, **40 test frontend**, Ruff, Python compile và production build đều qua. Đây là kết quả của bộ test tự động trên catalog demo, không phải phần trăm độ chính xác ngoài cửa hàng hay benchmark tải production.

## Thay đổi

| Khu vực | Hoàn thiện |
|---|---|
| Session | Lưu intent, sản phẩm đang hỏi, ràng buộc và trạng thái xác nhận. Session in-memory cô lập cho development/test; production chọn Redis và fail-closed nếu Redis lỗi. Đóng/hết hạn session xóa hội thoại và ngữ cảnh mua sắm. |
| Intent mua sắm | Hỗ trợ câu tiếng Việt và tiếng Anh phổ biến để xem giỏ, thêm/bớt/sửa số lượng, chuẩn bị checkout, xác nhận, hủy và hỏi trạng thái thanh toán. |
| Catalog và giỏ | Giá, tồn bán được, số lượng, giới hạn mặt hàng và tổng tiền do backend tính. Từ chối SKU hết hạn/hết hàng; hỏi lại khi có nhiều ứng viên. Idempotency key chặn yêu cầu thêm hàng bị gửi lặp. |
| Checkout sandbox | Tóm tắt có token xác nhận và thời hạn; backend kiểm tra lại giá/tồn và giữ lượng hàng trong sandbox. Trạng thái `PAID` chỉ đổi qua endpoint mô phỏng thanh toán; lời khách nói đã chuyển khoản không được xem là bằng chứng. |
| Cô lập dữ liệu | Cart và order kiểm tra `session_id`; session khác không đọc được order. Thanh toán sandbox xử lý event lặp có tính idempotent. |
| Kiosk | Thêm thẻ sản phẩm, xem/sửa giỏ, xác nhận checkout và nút mô phỏng thanh toán; xóa hội thoại khi phiên kết thúc. Avatar tải bất đồng bộ để giảm bundle chính. |
| Tài liệu | README ghi rõ phạm vi demo và giới hạn hạ tầng. |

## Luồng đã kiểm tra

1. Tạo hai session độc lập.
2. Khách thêm 2 lon Coca Zero; giá lấy từ catalog demo là 13.000đ/lon, tổng 26.000đ.
3. Gửi lại cùng một yêu cầu với cùng idempotency key; số lượng không bị nhân đôi.
4. Session thứ hai vẫn có giỏ rỗng và không truy cập được order của session đầu.
5. Chuẩn bị checkout, kiểm tra tổng, xác nhận rõ ràng và tạo order `PENDING`.
6. Câu khách tự khai đã chuyển khoản không cập nhật trạng thái. Hỏi trạng thái trả về `PENDING` cho đến khi endpoint sandbox nhận event mô phỏng.
7. Event thanh toán lặp được xử lý idempotent; event khác cố sửa trạng thái terminal bị từ chối.
8. SKU sữa đã quá hạn trong catalog demo bị từ chối khi thêm vào giỏ.
9. Kiểm tra câu địa phương “Cái chi bán đắt hàng nhứt?” không kích hoạt checkout.
10. Đóng/hết hạn session xóa hội thoại, sản phẩm gần nhất và memory mua sắm.

## Kết quả xác minh

| Lệnh | Kết quả |
|---|---|
| `CATALOG_REPOSITORY=demo .venv\Scripts\python.exe -m pytest -q` | **468 passed** |
| `npm run test:run -- --maxWorkers=2` trong `apps/kiosk-airi` | **40 passed**, 5 test files |
| `.venv\Scripts\ruff.exe check backend tests\test_sessions.py tests\test_shopping_flow.py` | Pass |
| `.venv\Scripts\python.exe -m compileall -q backend` | Pass |
| `npm run build` trong `apps/kiosk-airi` | Pass; bundle chính 167.04 kB (gzip 54.86 kB) |
| `git diff --check` | Pass |

Build vẫn cảnh báo chunk `model-viewer` khoảng 1.02 MB (gzip 290.30 kB). Chunk này đã được tách lazy-load khỏi bundle chính, nhưng vẫn lớn khi avatar được tải. JSDOM cũng in cảnh báo giả lập media/canvas; các test liên quan vẫn pass.

## Hạn chế cần giữ rõ

- Giỏ hàng, reservation, order và payment simulator hiện ở bộ nhớ của một process. Đây là **sandbox demo**, không lưu bền và không dùng chung giữa nhiều backend replica.
- Checkout bị tắt ở production. Không kết nối POS, tồn kho chi nhánh, cơ sở dữ liệu order hoặc cổng thanh toán; `PAID` trong test chỉ là kết quả mô phỏng.
- Chỉ cho phép ghi giỏ với catalog demo đã biết. Catalog WinMart nhập từ web là snapshot tham khảo, không đủ thẩm quyền để trừ tồn hay đặt hàng.
- Redis adapter có test fail-closed, nhưng không được xác minh với Redis đang chạy trong lượt này. PostgreSQL/Redis không được dùng cho test end-to-end; hạ tầng cửa hàng thật vẫn cần cấu hình và kiểm thử riêng.
- Không chạy benchmark gọi LLM trực tiếp trong lượt này. Bộ test đánh giá routing, hợp đồng tool, policy và kết quả có cấu trúc; nó không chứng minh tỷ lệ factuality trên khách hàng thật.
- Thống kê “bán chạy nhất”, “ngon nhất” hoặc sở thích khách hàng chỉ được nêu khi có dữ liệu bán/đánh giá được xác minh; catalog hiện tại không cung cấp các số liệu đó.

## Cơ sở thiết kế

- [ShoppingBench (2025)](https://arxiv.org/abs/2508.04266) đánh giá tác vụ mua sắm theo chuỗi bước. Áp dụng bằng cách kiểm tra cả hội thoại mua hàng, thay vì chỉ chấm câu trả lời độc lập.
- [Berkeley Function Calling Leaderboard](https://gorilla.cs.berkeley.edu/leaderboard) tập trung vào chọn và thực thi lời gọi hàm. Áp dụng bằng kiểm thử route intent, giới hạn quyền theo session và xác nhận hiệu lực của tool qua kết quả backend.
- [ViWOZ (2022)](https://arxiv.org/abs/2203.07742) cung cấp hội thoại tác vụ tiếng Việt nhiều lượt; dữ liệu không phải miền bán lẻ. Áp dụng ở mức thiết kế kiểm tra cách nói tự nhiên và lượt nối tiếp, không xem đây là bằng chứng độ chính xác retail.

## Chạy demo cục bộ

Trong terminal backend tại thư mục repo:

```powershell
$env:CATALOG_REPOSITORY = "demo"
.venv\Scripts\python.exe -m uvicorn backend.api.main:app --reload
```

Trong terminal frontend:

```powershell
cd apps\kiosk-airi
npm run dev
```

Thiết lập `CATALOG_REPOSITORY=demo` chỉ dành cho demo/test. Không thay URL database hoặc bật checkout sandbox như hệ thống thanh toán thật.
