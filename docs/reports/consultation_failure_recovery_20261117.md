# Lỗi “Sky chưa hoàn tất câu trả lời” — 17/11/2026

Bản sửa code: `b9f2e63`. Backend đã được khởi động lại từ worktree
`vietnamese-whisper-stt`, dùng catalog snapshot hiện hành; không đổi dữ liệu sang
fixture demo để lấy kết quả HTTP thành công.

## Nguyên nhân đã tái hiện

Đợt chẩn đoán trước sửa có hai câu bình thường trả HTTP 502: sữa tắm dịu nhẹ/mùi
không nồng và đồ uống khi hạn chế đường. Đây không chỉ là lỗi key. Có ba đường lỗi:

1. Planner dùng cả sở thích/công dụng làm tên hàng. Khi không khớp, truy xuất mở
   rộng sang ngành hàng quá rộng; hàng chứa một từ nguyên liệu/hương vị cũng có
   thể lọt vào. Ví dụ cacao thay trà, kẹo vị trà thay đồ uống, phở thay thịt.
2. Composer thêm công dụng không có bằng chứng. Renderer chặn; một số nhánh sửa
   và reviewer lại yêu cầu chọn SKU dù đang ở chế độ hướng dẫn không có hàng phù
   hợp. Hết lượt sửa thì exception bị API biến thành 502 chung.
3. Trong thử nghiệm thật cũng có HTTP 429. Khi reviewer hết khả năng gọi provider
   sau một draft bị chặn, không được trả lại draft đó như thể đã kiểm chứng.

Frontend gom phần lớn `/agent/respond` 5xx thành “Sky chưa hoàn tất câu trả lời”.
Thông báo đó không phân biệt được lỗi schema, truy xuất, render, review và quota.

## Thay đổi giữ lại

- API gọi `consult_customer`: sau số lượt sửa có giới hạn, lỗi provider/schema/
  render/review được xử lý thành một lượt trả lời ngắn, không chọn SKU mới. Các
  ràng buộc đã được kiểm chứng vẫn được lưu cho lượt sau. Đây là chế độ suy giảm,
  không được tính là đã tư vấn thành công.
- Giữ `consult` có khả năng báo lỗi cho các phép kiểm tra riêng của pipeline;
  không thay đổi hành vi theo việc transport có bị monkeypatch hay không.
- Tách từ sở thích khỏi loại hàng khi tạo lexical query; giữ nguyên hãng, biến
  thể trên nhãn, dung tích, ngân sách và các điều kiện bắt buộc.
- Khi mở rộng truy vấn trong một ngành hàng, giữ cụm loại hàng dài nhất có bằng
  chứng trong taxonomy, thay vì bỏ hoàn toàn loại hàng hoặc chỉ còn một từ.
- Dùng đúng head product và leaf taxonomy để loại các kết quả chỉ trùng nguyên
  liệu/hương vị. Nhận diện vỏ “lốc 6 chai/thùng…” khi xếp hạng, vẫn giữ nguyên SKU,
  quy cách và giá khi hiển thị.
- Khi câu có lựa chọn hợp lệ nhưng lời giải thích thêm tính chất chưa được chứng
  minh, có thể bỏ phần văn đó và giữ đúng ID/số lượng model đã chọn. Bản đã sửa
  vẫn qua renderer và reviewer. Không thay bằng cả tập kết quả truy xuất. Nếu
  review không chạy được sau bước này, không phát hành lựa chọn chưa được duyệt.
- Reviewer cho câu hướng dẫn sau no-match không bị buộc phải bịa ra một SKU.
- Chặn thêm việc suy từ nhãn “không đường” sang kết luận sinh lý như không tăng
  đường huyết, ổn định huyết áp hoặc phòng bệnh.
- Log lỗi có stage và trạng thái semantic rejection; không log exception thô hay
  key để giải thích lỗi cho khách.

Timeout, lỗi thiếu cấu hình và lỗi lập trình ngoài `GroqProviderError` vẫn được
phân biệt; bản sửa không biến mọi exception thành câu trả lời thành công.

## Kiểm thử

```text
1073 passed in 16.61s
```

29 ca trong `tests/test_consultation_failure_recovery.py` bao gồm truy xuất nhiều
ngành hàng, giữ filter/quy cách, tránh dầu ăn khi khách nói da dầu, giữ đúng lựa
chọn khi sửa lời giải thích, bắt buộc review, giữ ngân sách/ngữ cảnh và đi qua API
thật của ứng dụng. Full suite chạy với `CATALOG_REPOSITORY=demo` cho các fixture
cart/API; các ca snapshot tạo repository snapshot riêng.

## Đo trực tiếp sau khởi động lại

Gửi HTTP thật đến backend đang phục vụ demo ở `127.0.0.1:8000`. Tám câu với session
mới và một flow bốn lượt. Đo toàn bộ thời gian nhận JSON; không phải TTFT, không
bao gồm STT/TTS. Chạy tuần tự, không song song làm tăng quota.

| Câu/nhóm | Thời gian | Kết quả và giới hạn quan sát |
|---|---:|---|
| Chào hỏi | 0,007 s | Chào đúng |
| Xem sữa rửa mặt | 0,055 s | Có hàng/giá thật; chỉ đưa ví dụ, chưa đếm tổng |
| Sữa tắm dịu nhẹ, mùi không nồng | 3,753 s | Không lỗi; vẫn đưa danh sách chưa chứng minh độ dịu/mùi, chưa đạt |
| Đồ uống khi hạn chế đường | 5,059 s | Có nhãn không đường; còn chọn thùng lớn và lời dùng chưa thật sát |
| Thịt ăn lẩu | 2,778 s | Đúng nhóm thịt; thiếu tư vấn cách dùng theo dạng thịt |
| Ăn tiện, ít nấu | 3,735 s | Có lựa chọn; thiếu phân biệt món cần làm nóng |
| Đồ dọn nhà | 3,191 s | Chế độ suy giảm do provider/review hết quota, chưa tư vấn được |
| Da dầu, nhiều mụn | 0,498 s | Chế độ suy giảm; không được tính là tư vấn đạt |
| Dầu gội chống gàu dưới 150 nghìn | 0,022 s | Có SKU/giá đúng |
| Đổi sang mua cho nữ | 1,548 s | Chế độ suy giảm khi không hoàn tất review |
| Đổi sang từ 400ml | 0,036 s | Báo không chọn được loại vừa quy cách vừa ngân sách; giữ ràng buộc |
| Hỏi giá sau no-match | 0,008 s | Hỏi nhận diện hàng vì chưa có lựa chọn hiện hành |

**12/12 HTTP 200, không có popup 5xx trong mẫu này; 3/12 dùng `catalog-guidance`.**
Không gộp ba lượt suy giảm vào tỷ lệ tư vấn đúng. Các câu semantic có provider
hoạt động mất khoảng 2,78–5,06 giây trong mẫu cuối, chưa đạt mục tiêu dưới một
giây. Một lượt chẩn đoán trước đó từng lên khoảng 25 giây khi sửa/review nhiều
lần; không được suy từ mẫu cuối rằng outlier độ trễ đã hết.

[Toàn bộ câu hỏi, câu trả lời, ID/giá và thời gian](artifacts/consultation_failure_recovery_20261117.json).

## Đánh giá còn lại

Đã xử lý đường lỗi có thể biến câu hỏi mua hàng thường thành 502. Chưa thể kết
luận tư vấn đạt mức nhân viên thật: reviewer còn chấp nhận danh sách không chứng
minh tiêu chí chủ quan; Planner đôi lúc thêm loại/biến thể không được yêu cầu;
lời hướng dẫn có thể thừa hoặc không hợp cách dùng. Chế độ suy giảm chỉ giữ cuộc
hội thoại hoạt động, chưa thay thế được tư vấn của LLM.

Việc tiếp theo cần ưu tiên đo riêng độ chính xác intent và độ phù hợp lựa chọn,
giảm các request tuần tự không cải thiện câu trả lời, và xử lý quota theo từng
stage. Không tiếp tục nới guard hoặc bỏ reviewer để chỉ đạt HTTP 200. Lần này
không thay STT/VAD/TTS/LiveKit và không báo các luồng audio đã được kiểm chứng.
