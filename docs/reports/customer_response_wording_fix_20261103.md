# Sửa câu trả lời kiểu log và kiểm tra các nhánh tư vấn — 03/11/2026

Đã bỏ phần tự kể cách khớp tên hàng và các đuôi giá/tồn kho không liên quan
khỏi luồng gợi ý dùng chung, gồm cả combo và hỏi giá thấp nhất. Bản demo đã được
khởi động lại. Kết quả dưới đây đo wording; chưa chứng minh chất lượng nhân viên.

## Vấn đề và nguyên nhân

Câu người dùng phản ánh:

> Mình chọn theo tên hàng có nhắc “ăn lẩu”; cách dùng cụ thể cần kiểm tra trên
> nhãn. Bạn muốn xem loại này hay tìm thêm? Giá tại cửa hàng có thể khác
> catalog. Mình chưa kiểm tra được hàng còn tại cửa hàng.

Lỗi đến từ mẫu render dùng chung và nhánh fallback: trạng thái bằng chứng
nội bộ bị đọc thành lời tư vấn, rồi mẫu giá/tồn kho tự nối thêm nhiều câu dù
khách chỉ hỏi gợi ý hàng. Bộ lọc chi tiết còn hiểu nhầm phần ngân sách là thuộc
tính sản phẩm chưa xác minh, làm xuất hiện thêm câu thiếu dữ liệu ở các yêu cầu
đơn giản như “Tìm nước giặt dưới 150 nghìn”.

## Thay đổi

- `backend/api/main.py`: bỏ lời kể khớp tên/mô tả và đuôi giá/tồn khỏi mẫu gợi ý
  VI/EN/ZH. Combo và danh mục chưa xếp hạng cũng dùng giá tham khảo gọn; hỏi
  “rẻ nhất” trả mặt hàng và mức giá, giữ ngày/nguồn ở metadata.
- `backend/services/customer_consultation.py`: yêu cầu mô hình giữ cơ sở truy
  xuất trong nội bộ, giải thích theo nhu cầu khách; fallback bỏ kể tên nhóm
  và các đuôi kiểm tra tại quầy không cần thiết. Đầu ra tự kể việc chọn theo
  tên/nhãn bị bác để đi qua luồng sửa bản nháp.
- `backend/services/retail_tools.py`: tách ngân sách đã hiểu trước khi tìm các
  thuộc tính chưa được đáp ứng. Không bỏ qua các yêu cầu thật sự thiếu bằng
  chứng như độ phù hợp với trẻ nhỏ, da nhạy cảm hoặc kích cỡ.
- Sửa guard nhầm “Dạ, dầu gội…” thành lời đã thực hiện hành động “đã gọi”.
  Lời khẳng định đã gọi nhân viên khi chưa có hành động vẫn bị chặn.
- Trạng thái hội thoại chỉ ghi nhận thông tin thực sự đã nói. Gợi ý/ combo
  không đánh dấu rằng đã nói tồn kho; lượt khách hỏi tồn sau đó vẫn nhận câu
  trả lời đúng với dữ liệu hiện có.

Không thêm ngoại lệ cho lẩu, ngành thịt hoặc SKU cụ thể. Không biến khớp từ
trong tên thành bằng chứng điều trị, an toàn, độ ngon hoặc hiệu quả riêng của SKU.

## Bằng chứng wording trên nhiều ngành hàng

| Đợt | Cách chạy | Cụm log phát hiện | Có sản phẩm |
| --- | --- | ---: | ---: |
| Baseline sau lần sửa riêng câu lẩu ban đầu | HTTP, legacy/default route | 8/10 | 9/10 |
| Replay sau dọn mẫu chung | TestClient offline, snapshot thật | 0/10 | 9/10 |
| Replay trên mã cuối, thêm combo/giá thấp nhất/xếp vị | TestClient offline, snapshot thật | 0/13 | 11/13 |

Mười câu đầu của đợt 13 câu trùng bộ baseline. Đợt cuối tắt provider và luồng
consultation tạo sinh để kiểm tra trực tiếp các mẫu legacy, không dùng sản phẩm
giả. Các ngành gồm thịt, đồ uống, bánh quy, dầu gội, nước giặt, sữa, khăn giấy,
mì, kem đánh răng và nước rửa chén. Không so latency giữa baseline HTTP và
TestClient offline vì đây là hai cách chạy khác nhau.

Audit dò các cụm rõ ràng như “Mình chọn theo”, “Giá tại cửa hàng có thể khác
catalog”, “Tồn hiện tại chưa được xác minh”, “Catalog chưa xác nhận” và các
đuôi snapshot/combo tương tự. Không phát hiện cụm trong danh sách không đồng
nghĩa mọi câu đều tự nhiên hoặc đúng ngữ nghĩa. Không coi lượt không có đáp án
là tư vấn thành công.

Ví dụ thật trên mã cuối:

> Trong nhóm hàng này, giá tham khảo thấp nhất là 22.500đ: Xà bông Enchanteur sensation 90g.

Artifact:

- [Baseline 10 câu](artifacts/customer_wording_before_20261103.json)
- [Replay 10 câu sau sửa chính](artifacts/customer_wording_after_20261103.json)
- [Replay cuối 13 câu và hash nguồn](artifacts/customer_wording_after_final_20261103.json)
- [Probe bắt lỗi 9 nhánh/ngôn ngữ trước dọn bổ sung](artifacts/customer_wording_extended_red_20261103.xml)
- [27 regression qua cho các nhánh liên quan](artifacts/customer_wording_adjacent_routes_20261103.xml)

## Demo và kiểm tra provider thật

Demo: [http://127.0.0.1:5173/](http://127.0.0.1:5173/).
Backend được khởi động bằng `scripts/start_demo.ps1 -RestartBackend` để giữ
consultation, reviewer, local fallback và số lượng bán demo đang bật. Health và
frontend đều trả HTTP 200.

Đã quan sát một lượt thành công trên giao diện cho “Có loại thịt cho ăn lẩu không?”:

> Mình gợi ý Ba chỉ bò mỹ cắt cuộn chấm sốt sukiyaki Orifood khay 500g vì đã
> cắt cuộn sẵn, tiện cho lẩu không cần thái thêm. Kèm Thịt bò viên khay Hoa
> Doanh 300g nếu muốn thêm loại, bò viên cũng là món quen trong lẩu.

[Ảnh giao diện đã kiểm tra](artifacts/customer_wording_demo_20261103.jpg).
Đây là một lượt UI thành công được lưu trước đợt dọn bổ sung combo/xếp hạng,
không thay thế kết quả của đợt audit live riêng.

[Audit live 5 lượt sau sửa chính](artifacts/customer_wording_live_20261103.json) với
consultation/reviewer bật cho thấy:

- 1 lượt trả lời tạo sinh thành công.
- 2 lượt tra cứu local fallback, có sản phẩm nhưng được ghi nhận là degraded.
- 2 lượt unavailable, không chọn được sản phẩm.
- 0 cụm log bị dò; HTTP không lỗi không có nghĩa đủ chất lượng tư vấn.

[Audit live trên mã cuối](artifacts/customer_wording_live_final_20261103.json),
sau khởi động lại demo, có 2/5 lượt tư vấn với sản phẩm (lẩu và đồ uống), 1/5
lượt hỏi làm rõ yếu (dầu gội), 2/5 lượt unavailable (bánh quy và nước giặt),
0 lượt degraded, 0 lỗi HTTP và 0 cụm log bị dò. Lượt dầu gội không được tính là
tư vấn thành công: khách đã nêu rõ loại hàng/ngân sách nhưng hệ thống vẫn hỏi
lại mục đích hoặc tên hàng và chưa đưa lựa chọn cụ thể. Ngay cả hai lượt có
sản phẩm vẫn có lặp quy cách trong lời nói, cần chấm chất lượng riêng.

Log chẩn đoán ở đợt sửa chính ghi nhận rate limit, đầu ra có cấu trúc không hợp
lệ và lỗi validation nội bộ. Không quy toàn bộ thất bại cho quota. Hai lần
chạy live dùng cùng năm câu phát triển, không phải đánh giá mù hay bằng chứng
thống kê về tỷ lệ thành công ngoài thực tế.

## Điểm còn chưa đạt

- Legacy mode vẫn trả lời chung chung, không có sản phẩm cho “Gợi ý sữa tươi
  không đường”. Đợt này chưa xử lý lỗi định tuyến/bằng chứng đó; không nên đọc
  0/13 cụm log thành 13/13 câu tư vấn đạt yêu cầu.
- Một số câu legacy vẫn đọc danh sách nhiều món và hỏi theo mẫu. Cần đánh giá
  riêng mức độ hữu ích, chủ động, giữ ngữ cảnh và tư vấn theo nhu cầu.
- Live chưa ổn định: đợt cuối chỉ 2/5 lượt có tư vấn và sản phẩm, một câu hỏi
  lại yếu và hai lượt không tư vấn được. Cần sửa các lỗi provider/schema/validation còn lại và đánh
  giá trên câu hỏi mới do người thật viết, có người chấm độc lập.
- Các câu hỏi trực tiếp về tồn kho, nguồn giá, khuyến mãi hoặc thuộc tính chưa
  biết vẫn cần trả lời đúng giới hạn dữ liệu. Đợt này bỏ phần tự nối không liên
  quan, không xóa khả năng trả lời các câu hỏi đó.

## Kiểm thử và chạy lại

Backend cuối: **2.098 passed, 1 skipped, 0 failed**, một warning, trong 270,65 giây.
[XML nghiệm thu regression](artifacts/backend_full_customer_wording_delivery_20261103.xml).
Run đầy đủ trung gian có bốn lỗi assertion vì test cũ còn bắt buộc đọc đuôi
snapshot/tồn kho; đã đổi sang kiểm tra metadata ngày/nguồn và `stock_is_current`,
giữ nguyên kiểm tra SKU, giá thấp nhất, tổng tiền và danh mục. Cả bốn regression
[đã qua sau cập nhật contract](artifacts/customer_wording_updated_contracts_20261103.xml),
và run đầy đủ cuối ở trên qua toàn bộ. XML trung gian
`backend_full_customer_wording_final_20261103.xml` không phải kết quả nghiệm thu cuối.

Ruff cho nguồn/test/script thay đổi và `git diff --check` đã qua. Một warning
Starlette/httpx và test symlink bị bỏ qua trên Windows được ghi riêng; không
phải lỗi logic tư vấn. Test mới kiểm tra VI/EN/ZH cho combo, danh mục chưa xếp
hạng và giá thấp nhất; regression API kiểm tra câu lẩu, tồn kho hỏi tiếp,
ngân sách, tổng combo, giữ nguồn/ngày và không sửa giỏ hàng.

Chạy audit offline, dữ liệu snapshot thật:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evaluation.audit_customer_response_style --output docs/reports/artifacts/customer_wording_after_final_20261103.json
```

Chạy trên demo đang bật:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evaluation.audit_customer_response_style --base-url http://127.0.0.1:8000 --limit 5 --output docs/reports/artifacts/customer_wording_live_20261103.json
```

Artifact trước sửa và các run trung gian được giữ để đối chiếu. Bản XML được
chỉ rõ ở kết quả cuối là kết quả nghiệm thu regression cho mã bàn giao. Quyết
định production giữ **NOT READY**; sửa wording không thay thế đánh giá chất
lượng nhân viên, dữ liệu cửa hàng hiện tại hoặc nghiệm thu giọng nói.

