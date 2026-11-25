# Báo cáo tư vấn và giọng nói — 01/11/2026

**Đã nghiên cứu, sửa và kiểm thử kỹ thuật. Tư vấn vẫn chưa đạt mức nhân viên thật;
LiveKit chưa tích hợp và micro/loa phòng thật chưa được nghiệm thu.**

Demo development: http://127.0.0.1:5173/ — backend http://127.0.0.1:8000/.
Cờ `CUSTOMER_CONSULTATION_ENABLED` mặc định tắt; production bỏ qua cờ demo.
Không có bảng 35 câu → đáp án/SKU trong runtime.

## Các thay đổi

- Lựa chọn sản phẩm dùng schema typed: máy chủ kiểm tra ID, nhóm/vai trò, nhãn đường,
  quy cách, số lượng và tổng tiền. Giỏ tối đa sáu vai trò; lựa chọn thay thế tối đa hai
  món trong consultation. Controller catalog hiện có vẫn có thể trả ba lựa chọn.
- Sửa schema giỏ trước đây chỉ nhận hai pick dù policy cho phép sáu. Câu chọn một loại
  theo giá từng món trong phiên mới không còn được model tự biến thành giỏ nhiều món.
  Câu yêu cầu tổng ngân sách, số lượng hoặc nhiều thành phần vẫn giữ luồng giỏ.
- Khi nhóm rõ nhưng planner trả scope rỗng/hỏi lại, dùng controller catalog phù hợp;
  chỉ áp dụng trong phiên mới không có ràng buộc/ngữ cảnh cần giữ. Prefix tên loại
  phải thuộc cùng một nhánh taxonomy để tránh “đồ dùng” kéo hàng từ nhiều ngành.
- Chặn delegation/fallback làm rơi yêu cầu có mục đích sử dụng, thuộc tính, đối tượng
  hoặc phủ định vào tìm kiếm rộng. Guard nhận diện màu bao bì, lần mua ngoài phiên,
  quảng cáo và yêu cầu thay thế thiếu món gốc hỏi thêm danh tính.
- Log fallback có UUID phiên, nhãn nguyên nhân và bộ đếm schema/rate-limit/cooldown.
  Không ghi raw error, failed generation, khóa hay nội dung khách. Không thêm retry
  mù khi provider hết quota; diagnostic phân biệt lỗi contract với lỗi provider.
- VAD chỉ ngắt TTS tại `onSpeechRealStart`; fallback recorder từ chối câu vượt 12 s.
  Khi STT rỗng/non-speech/lặp sau ngắt, giữ câu đọc tối đa 15 s và phát lại bằng vé mới.
  Lời nói thật, stop, đổi phiên/ngôn ngữ và unmount đều xóa recovery.
- Cleanup upstream TTS/cancellation và AudioContext; file audio không giải mã được
  trả HTTP 422 và không retry như lỗi dịch vụ.

## Kết quả tư vấn thực tế

Lượt đủ 35 mới nhất:
[diagnostic_final35](artifacts/customer_consultation_35_diagnostic_final35_20261101.json).
35/35 HTTP 200, **9/35 có thẻ hàng**, 10 lượt có model scope-clarification. Log đối chiếu
UUID xác định 15 lượt consultation gặp lỗi: **13 rate-limit/cooldown, 1 structured
validation, 1 có cả schema và cooldown**. Số lỗi consultation khác số câu scope vì một
số lỗi còn đi xuống controller catalog. Xem [tổng hợp](artifacts/consultation_diagnostic_summary_20261101.json).

Đây là bộ câu đã lộ, mỗi câu bắt đầu phiên mới, không blind, chưa có nhân viên chấm.
Median 0,299 s, p95 4,536 s, max 6,792 s; chạy cùng máy với offline regressions nên
không dùng để tuyên bố cải thiện latency. Số thẻ hàng không phải điểm chất lượng.

Đọc nội dung thấy lỗi thật: đồ dùng cơ bản trả bàn chải trẻ em, snack cho trẻ trả món
cay, nhu cầu đồ ăn tiện bị ghép sáu món, và “tốt vừa tiền” bị tự giả định là thực phẩm.
Lượt này diễn ra **trước các guard delegation/fallback cuối**; artifact/hash được giữ
nguyên, không đổi nhãn thành kết quả source cuối. Lượt cũ 2/35 và 12/35 cũng là lịch sử,
không phải số cuối và không được chọn riêng lượt đẹp để công bố.

Sau guard cuối có replay scoped bảy câu, không thay thế một lượt đủ 35 mới:
[boundary_final7](artifacts/customer_consultation_35_boundary_final7_20261101.json).
7/7 HTTP 200 và không có thẻ hàng; nhiều câu vẫn hỏi chung khi provider lỗi, nên không
coi đây là chất lượng tốt hơn. Lượt này còn bắt được alias loại hàng nằm ở nhánh nam/nữ
khác nhau bị guard taxonomy chặn quá chặt. Sau sửa alias, replay riêng
[hai câu](artifacts/customer_consultation_35_alias_final2_20261101.json) trên source mới:
đồ dùng cơ bản được hỏi hướng bếp/vệ sinh/nhóm khác; dầu gội trả hai lựa chọn
80.000đ và 95.200đ, không ghép giỏ. Đây là các lượt nhỏ đã lộ, không chứng minh mọi
lỗi ngữ nghĩa đã giải quyết hay cải thiện ổn định.

## Kiểm thử kỹ thuật

- Full backend gần nhất: **1711 passed, 1 skipped, 1 warning**, 302,18 s.
  [Log v5](artifacts/consultation_full_regression_final_v5_20261101.txt). Lượt này bao gồm
  guard delegation/fallback, nhưng chạy trước sửa điều kiện alias cuối và regression API
  tương ứng. [Manifest](artifacts/final_boundary_source_manifest_20261101.json) ghi rõ
  `source_unchanged=false` cho hai file này; không công bố full v5 là source cuối.
- [Scoped consultation/Groq/API/parser/shopping flow trên source alias cuối](artifacts/consultation_final_alias_boundary_gate_20261101.txt)
  qua **754 passed, 1 warning**, 158,18 s; không có skip trong bộ này.
- Frontend **102 passed / 9 file** và production build qua; không đổi frontend sau
  mốc này. Build còn warning chunk model-viewer >500 KB.
- Ruff backend/tests/evaluation, compileall và diff-check qua. Skip full suite do
  Windows thiếu quyền tạo symlink (WinError 1314); warning là Starlette/httpx deprecation.

## STT, VAD, TTS và LiveKit

Luồng đang dùng: browser Silero v5/ONNX → WAV/MediaRecorder → Groq Whisper → agent →
Azure/ElevenLabs ticket stream → browser audio. **Repo không có LiveKit runtime,
dependency/config hoặc phòng/participant chạy thật.**

- Diagnostic người thật: FLEURS 12 clip WER **7,41%**, CER **4,43%**; ViVoice34 7 clip
  WER **21,30%**, CER **10,92%**. Là audio đọc công khai có tham chiếu, không phải retail
  microphone hay tập mù. Có mẫu STT gần deadline 20 s.
- Silero model thật có SPEECH_REAL_START cho 19/19 clip; silence/tone/noise không tạo
  speech. Một số đoạn đọc dài vượt 12 s; giới hạn này vẫn ảnh hưởng trải nghiệm.
- TTS vi/en/zh qua ticket/stream đã giải mã được; vé chỉ dùng một lần. Chưa có người
  nghe chấm phát âm số/tiền, ngữ điệu hay độ tự nhiên. E2E synthetic xác nhận transport,
  không xác nhận chất lượng tư vấn hoặc micro thực tế.
- API input controls cuối: im lặng/tone/noise trả transcript rỗng và
  `speech_detected=false`; file hỏng **422**, không còn 502 mơ hồ.
  [Artifact](artifacts/voice_input_controls_final_20261101.json).
- Voice provider/lifecycle regression trước telemetry: **73 passed**. Frontend mock
  tests không chứng minh echo/AEC/barge-in/autoplay trên thiết bị kiosk thật.

Computer Use đã dừng vì không xác định chắc URL của trình duyệt Windows trong phiên.
Mở tab demo chỉ được công cụ nhận `queued`; không tính đó là nghiệm thu UI/microphone.
Log demo còn ghi GET cart 503 khi browser khởi tạo; phần phụ thuộc giỏ hàng chưa được
xác nhận end-to-end trong lượt này.

## Nghiên cứu và phần còn thiếu

[Paper và hướng dẫn kỹ thuật](consultation_voice_research_20261101.md) đối chiếu
ProductAgent/PROCLARE, PEPPER, CRS survey, ALCE, GS1/nhãn hãng, Groq, Azure,
Silero/vad-web và LiveKit. [LiveKit readiness](livekit-turn-taking-readiness-20261101.md)
ghi rõ phần đề xuất và phần chưa triển khai. [Audit chi tiết](consultation_voice_audit_20261101.md)
giữ kết quả từng tầng ở các phiên bản trước.

Chưa đủ dữ liệu về cách dùng, hiệu quả, độ bền, độ lưu mùi, nhãn dị ứng và an toàn cho
phần lớn SKU; snapshot 15/09/2026 không chứng minh giá/tồn hiện tại. Paper/prompt và
một model reviewer không thay được dữ liệu này. Quota provider và contract vẫn làm
trải nghiệm thiếu ổn định. Tiếp theo cần nguồn nhãn đúng biến thể, provider đủ quota,
đánh giá mù hội thoại hoàn chỉnh bởi người Việt/nhân viên, và đo mic/loa/AEC/autoplay
trên kiosk thật. Giữ chế độ demo thử nghiệm; **chưa nghiệm thu “như nhân viên thật”**.

## Bổ sung sửa lỗi hỏi lặp — 01/11/2026

Đã xử lý lỗi trong ảnh “hỏi loại da” rồi vẫn hỏi lại bằng trạng thái typed dùng chung,
không gắn với da/mụn hay một ngành hàng: `pending_question` có `question_id`, `slot_key`,
`scope`, `options`; câu trả lời phải có trạng thái, `source_quote` khớp chính lượt khách
hiện tại và không được đổi slot. Câu hỏi mới được định danh theo scope + slot; câu hỏi đã
giải quyết được giữ trong `answered_slots`, câu trả lời sửa sẽ thay giá trị cũ. Khi planner
hỏi lại slot đã giải quyết, máy chủ lập kế hoạch lại một lần và chặn nếu vẫn hỏi lặp.
Với slot chưa được trả lời, phản hồi đưa lựa chọn hoặc hướng bỏ qua tiêu chí.
Nhánh identity,
delegate, selection, review và fallback đều lưu cùng trạng thái. Lỗi provider giữ nguyên
câu trả lời thô và `source_turn_id`; lượt “tiếp/thử lại” dùng lại câu trả lời đó thay vì
bắt khách nói lại.

Regression mới:

- `tests/test_clarification_api.py`: 2 kiểm thử HTTP, gồm đúng luồng “da dầu nha” và lỗi
  provider trong lúc trả lời.
- `tests/test_clarification_recovery.py`: 16 kiểm thử state/retry/correction/topic switch,
  nguồn trích dẫn, câu trả lời không biết, câu hỏi lồng và không crash khi planner lặp.
- Mốc source cuối: 526 bài scoped backend/API/session/consultation qua, 1 warning
  Starlette/httpx; full backend trước bổ sung cuối là 1.724 passed, 1 skipped do Windows
  không cho tạo symlink, 1 warning. Ruff và `py_compile` qua cho các file sửa.
- Full backend mã cuối: **1.737 passed, 1 skipped, 1 warning**; log tại
  [full_final](artifacts/clarification_full_backend_final_20261101.txt).

Probe thật qua SSE nằm ở
`artifacts/clarification_repeat_verified_20261101.json`. HTTP và frontend demo đều chạy
ở `http://127.0.0.1:8000` và `http://127.0.0.1:5173/`. Probe ghi nhận các lượt 200 và
không lặp nguyên câu; chất lượng chọn sản phẩm chưa được kết luận vì Groq hết quota TPD
trong lúc chạy (`rate_limit_exceeded`). Artifact không được dùng để tuyên bố tư vấn đã
đạt mức nhân viên thật.
