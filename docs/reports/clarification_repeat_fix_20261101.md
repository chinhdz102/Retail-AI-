# Sửa lỗi hỏi lại thông tin khách đã trả lời — 01/11/2026

Đã sửa cơ chế lưu và kiểm tra câu hỏi–câu trả lời, bổ sung kiểm thử qua HTTP/SSE và
khởi động lại demo trên mã cuối. **Chưa nghiệm thu chất lượng tư vấn như nhân viên thật:**
lượt chạy dịch vụ thật vẫn bị giới hạn quota Groq và còn câu hỏi ít hữu ích.

## Nguyên nhân

Ảnh khách gửi cho thấy trợ lý hỏi loại da, nhận “da dầu nha” rồi hỏi lại nguyên câu.
Frontend vẫn gửi cùng phiên; lịch sử không bị mất. Backend trước đây chỉ giữ
`last_question` bằng văn bản, không kiểm tra câu trả lời đã giải quyết thuộc tính nào.
Nhánh clarification trả thẳng câu của model nên chỉ dặn prompt “đừng hỏi lại” chưa đủ.

Một lỗi nữa được xác nhận qua trace thật: model chỉ điền
`pending_question.question`, bỏ trống `question` ở ngoài. Bộ xử lý cũ thay câu hỏi thật
bằng câu chung chung “cho biết tên loại hàng/mục đích”. Model còn có thể bỏ `need`, làm
phạm vi tư vấn trống. Đã xử lý cả hai trường hợp này.

## Thay đổi

- Câu hỏi đang chờ có ID do server tạo, slot, phạm vi, câu hỏi và lựa chọn. Câu hỏi đã
  trả lời được lưu riêng; câu trả lời sửa thay giá trị cũ. Không có bảng câu hỏi/đáp án,
  SKU hay nhánh riêng cho da dầu trong mã chạy.
- Chấp nhận câu trả lời khi ID/slot khớp trạng thái server, trích dẫn khớp lời khách;
  không nhận trích dẫn bịa, chỉ dấu câu hoặc “không biết” thành thông tin đã biết.
- Khi model hỏi lại thông tin đã giải quyết, thử lập kế hoạch lại một lần. Chặn câu
  hỏi lặp ở planner, chọn hàng và review; không chỉ chặn câu giống từng chữ.
- Với câu chưa được trả lời, cung cấp lựa chọn hoặc hướng bỏ qua tiêu chí thay vì phát
  lại nguyên câu hỏi. Đây là phương án dự phòng; mức tự nhiên của nó còn cần cải thiện.
- Giữ câu trả lời thô và ID lượt khi provider lỗi; “tiếp/thử lại” dùng lại câu trả lời
  gốc. Lỗi lặp lần nữa không ghi đè nó bằng từ “tiếp”. Giữ cả yêu cầu đầu nếu lỗi xảy
  ra trước khi có câu hỏi. Không biến lỗi provider thành yêu cầu khách nhắc lại nhóm hàng.
- Giữ ngân sách/quy cách trên lượt thử lại; tránh mang ràng buộc sang nhóm hàng mới.
  Dữ liệu phiên cũ có `last_question` được chuyển đổi mà không sửa đối tượng trước khi lưu.

## Kiểm thử và bằng chứng

| Phạm vi | Kết quả | Giới hạn |
|---|---|---|
| Backend toàn bộ mã cuối | **1.737 passed, 1 skipped, 1 warning** | Skip do Windows không cho tạo symlink |
| Consultation/API/session trên mã cuối | **526 passed, 1 warning** | Provider được mock, kiểm tra logic và luồng ứng dụng |
| HTTP/SSE đúng dạng lỗi trong ảnh | Qua | Model giả lập cố hỏi lại slot đã trả lời; server sửa kế hoạch và giữ câu trả lời |
| Các ngành khác | Qua | Bút, nồi, nước lau sàn, đèn; không chứng minh mọi ngành/tình huống |
| Ruff và biên dịch Python | Qua | Các file liên quan thay đổi |

Hai file mới có 18 test HTTP/state/recovery. Có trường hợp đổi chủ đề, sửa câu trả lời,
trích dẫn sai, không biết, trả lời xong hỏi một thông tin khác, hỏi lại ở bước chọn hàng,
retry thành công, hai lần lỗi liên tiếp và câu hỏi chỉ xuất hiện ở trường lồng.

- [Log scoped cuối](artifacts/clarification_scoped_final_20261101.txt)
- [Log full backend trước sửa cuối](artifacts/clarification_full_backend_20261101.txt)
- [Tái hiện trước sửa](artifacts/clarification_repeat_before_20261101.json)
- [Probe SSE mã cuối, kèm hash nguồn](artifacts/clarification_repeat_verified_20261101.json)

Log toàn bộ cuối: `artifacts/clarification_full_backend_final_20261101.txt`.
Warning do Starlette/httpx deprecation.

## Kết quả chạy thật

Probe mã cuối có 3 hành trình, 8 lượt: chăm sóc da, đồ dọn nhà và đồ ăn. Cả 8 lượt HTTP
200 nhưng 5 lượt rơi vào phản hồi chưa tra được hàng; log xác định
`provider-rate-limit`. Không có thẻ sản phẩm trong lượt này. HTTP 200 không phải điểm
chất lượng tư vấn.

Model chọn câu đầu khác ảnh (hỏi sản phẩm đang dùng), nên câu “da dầu nha” trong probe
này không trực tiếp trả lời câu đang chờ. Không thể dùng lượt này để tuyên bố đã nghiệm
thu nguyên hội thoại ảnh với model thật. Kiểm thử HTTP giả lập đã kiểm đúng trạng thái
“hỏi loại da → da dầu → model vẫn cố hỏi loại da”; kiểm thử đó qua.

Trace riêng xác nhận lỗi câu hỏi lồng đã được sửa: câu hỏi loại da hiện được lấy đúng
từ `pending_question.question`. Lượt tiếp theo bị quota TPD. Không đổi model hoặc tăng
quota/chi phí tài khoản trong lần sửa này.

Demo đang chạy ở [frontend](http://127.0.0.1:5173/) và
[backend](http://127.0.0.1:8000/), bật consultation development. Chưa kiểm lại microphone,
loa hay echo thực trong lần sửa lỗi hội thoại này. Tình trạng STT/TTS/VAD và LiveKit giữ
theo [báo cáo tổng](consultation_voice_completion_20261101.md): LiveKit chưa tích hợp;
không có cơ sở nâng mức nghiệm thu giọng nói.

Phần còn cần làm để đạt mục tiêu nhân viên thật: provider đủ quota, kiểm tra hội thoại
nhiều lượt bằng người đánh giá, dữ liệu nhãn đúng biến thể và kiểm thử mic/loa/AEC trên
thiết bị thật. Các giới hạn này không được thay bằng kết quả unit test.
