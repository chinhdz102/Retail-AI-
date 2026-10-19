# Báo cáo kiểm thử luồng chăm sóc khách hàng Retail AI

- **Ngày kiểm thử:** 19/10/2026
- **Phạm vi:** hội thoại hỗ trợ khách, chuyển yêu cầu cho nhân viên (mô phỏng), session mua sắm, catalog demo, giỏ hàng và checkout sandbox.
- **Môi trường:** Python backend và Vue kiosk; catalog demo 6 sản phẩm; không dùng thanh toán thật.

## Kết quả tóm tắt

| Hạng mục | Kết quả |
|---|---:|
| Backend tự động | **487 passed** |
| Frontend tự động | **41 passed** |
| Kịch bản shopping-flow ASGI | **20/20 passed** (10 kịch bản × 2 lượt) |
| Báo cáo benchmark | [shopping_flow_sandbox_20261019T081306Z.json](../../evaluation/results/shopping_flow_sandbox_20261019T081306Z.json) |
| Ruff | Pass |
| Python compileall | Pass |
| Production build | Pass |
| `git diff --check` | Pass |

Benchmark sandbox ghi nhận 98 API call, P50 **0.892 ms**, P95 **8.509 ms**, trung bình **3.040 ms** trong ASGI chạy cùng process. Đây là độ trễ nội bộ của benchmark, không phải độ trễ mạng hoặc production. Benchmark không gọi LLM (0 lần), nên các con số không đo tốc độ hay chất lượng phản hồi của Groq.

Trong 4 lần kiểm tra lời khách tự khai đã thanh toán, hệ thống không xác nhận nhầm lần nào. Trong 4 lần kiểm tra truy cập chéo session, không có dữ liệu giỏ/đơn bị lộ.

## Luồng chăm sóc khách hàng

| Trường hợp | Kết quả đã xác minh |
|---|---|
| Hỏi chính sách thành viên, đổi trả hoặc giờ hoạt động | Không tự tạo ticket và không khẳng định chính sách không có trong nguồn đã xác minh. |
| Báo bị tính tiền hai lần, phản ánh sự cố sản phẩm hoặc yêu cầu gặp quản lý | Tạo ticket demo theo session, hiển thị mã/trạng thái, nhận diện loại vấn đề và không gọi LLM. |
| Gửi lại cùng yêu cầu | Idempotency trả lại ticket cũ, không tạo ticket thứ hai. |
| Tra cứu ticket | Chủ session lấy được ticket; session khác nhận 404. |
| Báo cáo chứa số điện thoại | Số điện thoại của kịch bản kiểm thử được che trong mô tả ticket. |
| Prompt injection hoặc câu bảo trợ lý bỏ quy tắc | Không tạo ticket chỉ dựa trên nội dung injection; câu hỏi không có báo cáo cụ thể đi theo fallback an toàn. |
| Tiếp tục mua sau khi chuyển hỗ trợ | Có thể quay về trạng thái mua sắm trước đó; retry ticket không làm mất trạng thái quay lại. |

Ticket hiện mang trạng thái `RECEIVED_DEMO`. Nó được giữ trong bộ nhớ của một process và **không gửi thông báo cho nhân viên thật**. Giao diện và phản hồi nói rõ giới hạn này để không khiến khách tưởng yêu cầu đã được nhân viên tiếp nhận.

## Kịch bản shopping-flow

Benchmark xác minh các luồng liên quan để đảm bảo trợ lý không biến hội thoại hỗ trợ thành thao tác mua hàng sai:

1. Giữ danh mục, giới hạn giá và yêu cầu không cay xuyên nhiều lượt tư vấn.
2. Thêm nhiều sản phẩm một lần; retry cùng mã lượt không nhân đôi số lượng.
3. Nếu một SKU không bán được, từ chối cả lô và giữ giỏ nguyên vẹn.
4. Xác nhận đơn và kiểm tra số tiền lấy từ catalog demo.
5. Lời khách nói “đã thanh toán” không đổi trạng thái `PENDING`; chỉ endpoint thanh toán sandbox tạo trạng thái mô phỏng.
6. Giỏ có thể tiếp tục sau khi thanh toán thất bại hoặc đơn hết hạn; order cũ vẫn giữ trạng thái để tra cứu.
7. Chuyển nhân viên, đọc lại ticket, chống injection, cô lập session và retry đồng thời.

Các câu hỏi giá, tồn, khuyến mãi và vị trí chỉ được trả lời từ catalog/snapshot hoặc nguồn chi nhánh đã cấu hình. Test hiện có xác minh rằng dữ liệu khuyến mãi cần snapshot hợp lệ, thông tin nhập hàng cần receipt event và thông tin cửa hàng/địa điểm phải có nguồn đã xác minh. Khi thiếu nguồn, agent cần nói rõ chưa có dữ liệu thay vì tự bịa. Đây không phải bằng chứng rằng toàn bộ catalog sản phẩm ngoài cửa hàng đã chính xác.

## Tệp thay đổi chính

- Backend thêm bộ phân loại báo cáo/handoff, tạo và đọc ticket theo session, khử thông tin liên hệ, hỗ trợ idempotency và tiếp tục phiên mua sắm.
- Kiosk hiển thị nút yêu cầu nhân viên ở chế độ demo, mã/trạng thái ticket và thông báo rõ không liên hệ nhân viên thật.
- Shopping flow hỗ trợ nhiều món trong một câu với ghi giỏ nguyên tử, duy trì constraints qua các lượt, gia hạn session khi hoạt động và khôi phục giỏ khi order thất bại/hết hạn.
- Bộ benchmark có 10 kịch bản xác định, xuất JSON theo tên có thời gian và không ghi đè kết quả cũ theo mặc định.

## Giới hạn và việc còn lại

- Bộ benchmark chạy trực tiếp trên ASGI nội bộ, catalog demo và không có LLM, PostgreSQL, Redis hay mạng thật. Nó không đo factuality của model trực tiếp, tải đồng thời ở production, giọng nói, hoặc chất lượng nhận dạng giọng địa phương.
- Ticket, giỏ, order và thanh toán sandbox hiện là dữ liệu trong bộ nhớ. Muốn dùng thật cần kho lưu bền, tích hợp CRM/POS và xác nhận quy trình nhận ticket với nhân viên.
- Cần chạy thêm bộ hội thoại với Groq thật và dữ liệu catalog được chủ cửa hàng xác nhận trước khi kết luận về chất lượng tư vấn thực tế.
- Build thành công nhưng Vite vẫn cảnh báo chunk `model-viewer` khoảng 1.02 MB (gzip 290.30 kB). Vitest cũng in cảnh báo JSDOM không mô phỏng media/canvas; 41 test vẫn pass.

## Cách chạy lại

```powershell
$env:CATALOG_REPOSITORY = "demo"
.venv\Scripts\python.exe -m pytest -q --tb=short
.venv\Scripts\ruff.exe check backend tests scripts evaluation
.venv\Scripts\python.exe -m compileall -q backend tests scripts evaluation
.venv\Scripts\python.exe evaluation\run_shopping_flow_benchmark.py --trials 2
```

```powershell
cd apps\kiosk-airi
npm run test:run
npm run build
```
