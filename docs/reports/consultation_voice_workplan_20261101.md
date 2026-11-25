# Kế hoạch nâng chất lượng tư vấn và kiểm tra giọng nói

Ngày bắt đầu: 01/11/2026. Trạng thái: hoàn tất phần triển khai và kiểm thử kỹ thuật; chưa đạt nghiệm thu chất lượng nhân viên.

Cập nhật cuối đợt: đã hoàn tất nghiên cứu, sửa các lỗi tái hiện được và các vòng kiểm
tra mô tả trong [báo cáo](consultation_voice_audit_20261101.md). **Chưa đạt tiêu chí chất
lượng tư vấn để nghiệm thu**; chưa xác nhận micro/loa/browser thật. Các mục dưới là kế
hoạch và acceptance criteria, không có nghĩa mọi tiêu chí đã qua.

## Mục tiêu và tiêu chí

Khách được hướng dẫn chọn hàng đúng nhu cầu, nhận một câu hỏi có ích khi thật sự thiếu
thông tin, và tiếp tục được hội thoại mà không phải nhắc lại sản phẩm/ngân sách. Lời
ngắn, có lý do chọn thực tế, không tự nhận trải nghiệm hay tự thêm công dụng. Không
đánh đồng lịch sự, HTTP 200 hay số test qua với “như nhân viên thật”.

Không viết đáp án riêng cho 35 câu. Chúng là development set đã lộ. Thêm các cách
diễn đạt/nhóm hàng và hội thoại mới để tìm lỗi; agent tự chấm vẫn phải ghi là tự chấm.
Mức giống nhân viên cần người dùng/nhân viên thật đánh giá, chưa thể tự chứng nhận.

## 1. Đóng băng hiện trạng và bản đồ hệ thống

- Giữ artifacts cũ, lưu hash mã/phiên bản mới; tránh ghi đè bằng một lần chạy đẹp.
- Kiểm tra catalog, enrichment và nguồn cho từng loại phát biểu; tách thông tin còn
  thiếu với lỗi không dùng dữ liệu đã có.
- Kiểm tra kiến trúc voice thực tế: Silero browser VAD → Groq Whisper hoàn tất đoạn
  → agent → Azure/ElevenLabs streaming TTS → browser. Hiện **không có runtime LiveKit**.
- Không di chuyển sang LiveKit chỉ để đổi tên luồng. Đánh giá khoảng trống và tính cần
  thiết từ tài liệu trước; phần chưa triển khai không được báo là đã test thành công.

## 2. Nghiên cứu có nguồn và thiết kế

- Đọc tiếp nguồn gốc: ProductAgent/PROCLARE, PEPPER, CRS survey, nghiên cứu citation
  grounding. Ghi cụ thể ý nào áp dụng, chi phí và giới hạn, không lấy điểm paper làm
  điểm ứng dụng.
- Đối chiếu GS1/Codex và khai báo manufacturer thật. Dùng registry có barcode, đúng
  biến thể/dòng, nguồn, thời điểm, trạng thái. Claim marketing không thành bằng chứng
  hiệu quả hoặc an toàn. Không sinh dữ liệu nhãn để lấp chỗ trống.
- Đọc tài liệu Groq STT, Azure TTS/SSML, Silero/vad-web và LiveKit về endpointing,
  interruption, false interruptions và cleanup; ghi rõ hỗ trợ tiếng Việt theo nguồn.

## 3. Sửa nền tảng hội thoại

- Tách quyết định cần gì/tìm gì/chọn gì khỏi viết lời. Dùng bộ nhớ hiện có để giữ
  sản phẩm, điều kiện và thông tin đã hỏi, nhưng bỏ phạm vi cũ khi khách đổi nhu cầu.
- Đưa bằng chứng thuộc tính vào synthesis. Không để model viết tự do công dụng của
  SKU rồi chỉ chặn một vài cụm từ bằng regex.
- Kiểm tra đầu ra bị cắt, số liệu/quy cách, đơn vị gói và lý do chọn; không công bố
  một tool call có câu văn dang dở như câu trả lời hoàn chỉnh.
- Kiểm tra giỏ theo công việc/nhu cầu, số người/bữa, hàng có sẵn và mức ngân sách;
  không lấy các món rẻ nhất khác nhóm ghép thành một bữa ăn.
- Đưa bước hỏi ngắn có hướng chọn thay cho hỏi vòng hoặc từ chối dữ liệu chung chung.

## 4. Kiểm chứng tư vấn

- Contract/HTTP tests cho lỗi logic và các ngành khác ví dụ đã cho; negative controls
  cho đổi nhóm, thiếu nguồn, chỉ định phủ định và dữ liệu có câu lệnh giả.
- Chạy lại 35 câu với mô hình thật sau khi thay đổi ổn định, rồi chạy journeys mới.
  Ghi actual model/fallback, độ trễ, lỗi provider, ID/thẻ hàng, nội dung và hash nguồn.
- Chấm riêng: đúng nhu cầu, dữ kiện đúng, hỏi đúng chỗ, nối được lượt, dễ đọc/nghe.
  Bất kỳ bịa thuộc tính, sai nhóm, mất ràng buộc quan trọng hoặc giỏ vô lý đều là lỗi.
- Dừng mở rộng test khi chỉ lặp lại lỗi đã biết; sửa nguyên nhân rồi chạy lại phần
  bị ảnh hưởng. Không tuyên bố bao phủ mọi cách hỏi hay mọi mặt hàng.

## 5. Voice: test theo tầng rồi test xuyên suốt

1. STT: âm thanh người thật có bản chép tham chiếu/giấy phép (Bắc/Trung/Nam), câu
   retail TTS tổng hợp ghi rõ là synthetic, im lặng/âm nhiễu/đoạn ngắn, sai định dạng,
   timeout và session cũ. Đo WER/CER, false accept/reject, thời gian và fallback.
2. VAD: asset/model/WASM/worklet thật, pre-roll, pause trong câu, đoạn rất ngắn/dài,
   lỗi load, quyền mic, đổi thiết bị, hủy khi đang tải và cleanup. Test giả lập không
   thay cho chạy model thật trên audio. Kiểm tra riêng echo/self-capture thực tế.
3. TTS: trực tiếp và ticket→stream, tiếng Việt/Anh/Trung, SSML escape, tiền/đơn vị,
   audio decode, first-byte, kết thúc, lỗi giữa stream, hủy, autoplay và browser fallback.
4. End-to-end: audio → transcript → trả lời có thẻ hàng → TTS decode/playback; đổi
   ngôn ngữ, đóng phiên, callback cũ, retry. Phân biệt replay audio với micro/loa phòng
   thật; thiếu phần nào phải ghi chưa xác nhận.
5. LiveKit: kiểm tra dependency/config/runtime; nếu chưa có thì đối chiếu thiết kế
   và ghi “chưa triển khai”, không làm migration lớn khi chưa có nhu cầu đo được.

## 6. Báo cáo và điều kiện chốt

- Báo cáo cập nhật theo findings, sửa gì, test nào dùng mock/real, dữ liệu/giấy phép,
  nguồn/hash, kết quả từng case và lỗi còn lại; liên kết master report.
- Giữ demo chạy trên phiên bản đã xác minh, không sửa hoặc tạo đơn hàng thật.
- Chỉ bật mặc định khi các lỗi ngữ nghĩa quan trọng được giải quyết và có bằng chứng
  đánh giá độc lập phù hợp. Nếu chưa đạt, giữ experimental và nêu rõ phần chưa đạt.

## Nhật ký

- Đã kiểm tra mã và dependencies: không có LiveKit; tài liệu voice có thông số VAD
  cũ (800/400/1400 ms, 30 s) khác mã đang chạy (600/250/900 ms, 12 s). Cần sửa tài liệu
  và kiểm tra hành vi theo cấu hình thật.
- Đã đối chiếu ALCE, nguồn hãng, Groq, Azure, vad-web/Silero và LiveKit; manifest lưu
  hash. Đã thêm evidence projection và draft review, nhưng reviewer chấp thuận sai
  trong live run, nên chưa coi đây là lời giải đủ tin cậy.
- Đã chạy 35 câu thật, 6 journeys/16 lượt mới, 19 file người thật STT + VAD, control
  im lặng/nhiễu/file lỗi, TTS 3 locale và 4 E2E tổng hợp. Bản gốc/artifacts được giữ lại.
- Đã sửa interruption onset, overlong fallback, cancellation TTS, AudioContext cleanup,
  finish reason/cooldown, giới hạn copy audio, định tuyến lookup và phủ định/tham chiếu
  tiếng Việt. Các phiên bản từng run có hash riêng; không gộp thành một điểm cuối.
- Micro/loa phòng, browser AudioWorklet/autoplay thật và chất lượng nghe TTS chưa được
  xác nhận. LiveKit chưa tích hợp. Phần còn phải làm được ưu tiên trong báo cáo, không
  đánh dấu hoàn thành chất lượng nhân viên chỉ vì code tests qua.


## Cập nhật hoàn tất 01/11/2026

Đã thêm typed selection, parser, schema giỏ nhiều vai trò, guard nhận diện, guard giỏ
không do khách yêu cầu, fallback giữ ràng buộc và diagnostic nguyên nhân provider/contract.
Lượt đủ 35 gần nhất là
[`diagnostic_final35`](artifacts/customer_consultation_35_diagnostic_final35_20261101.json):
35 HTTP 200, **9/35 có thẻ hàng**; đọc nội dung vẫn thấy lỗi nghiệp vụ. Log xác định
13 lỗi rate-limit/cooldown, 1 structured-validation, 1 mixed schema/cooldown. Các số
12/35 và 2/35 thuộc lượt trước, không phải số cuối. Lượt đủ 35 này diễn ra trước guard
delegation/fallback cuối; sau đó replay bảy câu riêng và chạy regression phù hợp.
Kết quả/hash/log từng mốc ở [báo cáo hoàn tất](consultation_voice_completion_20261101.md).
Full backend trước alias cuối **1711 passed, 1 skipped, 1 warning**; bộ ảnh hưởng sau
alias cuối **754 passed, 1 warning**. Frontend **102 passed**, build qua.
Chưa đạt chất lượng nhân viên; browser mic/loa/
AEC/autoplay và LiveKit chưa nghiệm thu. Các tiêu chí chất lượng trong kế hoạch vẫn
chưa hoàn tất, dù phần nghiên cứu/sửa lỗi/kiểm thử kỹ thuật đã được thực hiện.
