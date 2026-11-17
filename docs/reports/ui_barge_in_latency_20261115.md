# Giao diện, ngắt lời và độ trễ — 15/11/2026

## Kết quả

- Làm lại giao diện bằng bảng màu xanh đậm và mint, thanh nhận diện Sky,
  trạng thái ngắn, khung hội thoại và thẻ sản phẩm rõ hơn. Gộp chọn micro,
  ngôn ngữ và thao tác phiên vào bảng cài đặt thu gọn.
- Giữ khung chữ đóng khi mới mở; bật hội thoại giọng nói tự mở phần chữ để
  thấy lời nhận diện. Bố cục desktop chia hai phần; màn hình nhỏ đặt avatar
  phía trên khung hội thoại. Kiểm tra trực quan tại 1280×820, 702×771,
  390×844, gồm bảng cài đặt.
- Đổi khoảng cách camera avatar sang phần trăm khung model để tránh cắt
  đầu/thân sau khi model 3D tải xong hoặc đổi kích thước cửa sổ.
- Sửa nguyên nhân không ngắt lời: trước đây `speak()` đóng micro suốt TTS.
  Nay micro có AEC và Silero được mở ngay khi chuẩn bị giọng nói. Sau khi
  Silero xác nhận lời nói, hủy ticket đang chờ, dừng audio hoặc browser
  synthesis, hủy tải media và giữ capture/pre-roll cho câu mới.
- Giữ capture đang mở khi TTS kết thúc, tránh đóng/mở lại mic giữa hai lượt.
  Bỏ callback phát âm thanh cũ để nó không đổi trạng thái câu nói mới.
- Lỗi micro trong lúc TTS cũng dừng âm thanh; không để Sky tiếp tục nói khi
  giao diện đã chuyển sang trạng thái lỗi và ẩn nút dừng.
- STT Azure dùng lại HTTP client/TCP/TLS, đóng client do service sở hữu
  khi backend tắt. Không thay đổi transcript, ngưỡng kiểm tra hoặc âm thanh.
- Lỗi dịch vụ tư vấn trên UI được chuyển thành thông báo tiếng Việt ngắn.

## Streaming thực tế

| Chặng | Đang chạy | Giới hạn |
|---|---|---|
| Thu âm + VAD | AudioWorklet PCM và Silero tại trình duyệt | Không phải STT |
| STT | Gửi WAV sau khi VAD kết thúc câu → Azure `conversation`, `vi-VN` | Batch, chưa có partial transcript |
| LLM tư vấn | Trả một JSON sau các bước xử lý tư vấn | Chưa stream token/câu ra giao diện |
| TTS | Ticket một lần → HTTP MP3 streaming Azure | Có streaming; cần chờ LLM cung cấp nội dung trước |

Luồng kiosk đang kiểm tra không đi qua LiveKit. Không gọi kết quả này là
kiểm tra phòng LiveKit hoặc streaming toàn bộ hội thoại.

VAD giữ 640 ms trước lời nói và đợi 1440 ms không có lời nói để kết thúc.
Giữ thời gian đợi này vì đã có ca người dùng nghỉ khoảng một giây rồi nói
tiếp hoặc sửa ý; giảm đơn thuần sẽ làm mất đoạn sửa ý. Khi nói chen, Sky
dừng ở lúc xác nhận bắt đầu nói, không chờ hết khoảng im lặng này hoặc STT.

## Đo API trên máy demo

Đo sau khi khởi động lại backend với code mới, gọi thật localhost và Azure.
Không gọi LLM trong phép đo. Chỉ hai mẫu STT và hai câu TTS; số dưới đây
không phải p50/p95, benchmark giọng thật hoặc so sánh trước/sau có kiểm soát.

| Mẫu | Đo được |
|---|---|
| STT, WAV giọng tổng hợp Nam Minh 4,448 s, lượt đầu | 2576 ms từ lúc gửi upload tới JSON |
| STT, WAV giọng tổng hợp Nam Minh 3,680 s, lượt sau | 858 ms |
| TTS 40 ký tự | Ticket 4 ms; byte đầu 1360 ms; hoàn tất 1709 ms; 27 gói |
| TTS 123 ký tự | Ticket 5 ms; byte đầu 841 ms; hoàn tất 1521 ms; 52 gói |

Hai STT trả đúng các câu mẫu “Mình cần nước lau sàn, không phải nước giặt.”
và “Cho mình thịt bò để nhúng lẩu.” Không suy ra tất cả dấu hoặc giọng nói
đều đã đúng. TTS có `Transfer-Encoding: chunked`, `audio/mpeg` và nhiều
gói đến trước khi hoàn tất; byte đầu không đồng nghĩa thời điểm loa phát.

Các thời gian trên chưa gồm thời gian người dùng nói, đợi VAD 1440 ms,
LLM và buffer phát âm thanh. Client được tái sử dụng đã được kiểm tra;
chưa có phép đo kiểm soát để kết luận tiết kiệm chính xác bao nhiêu ms.

## Kiểm tra ngắt lời trong trình duyệt

Chạy trang demo thật trong IAB với Silero/WASM/AudioWorklet thật và TTS
Azure thật. Đầu vào micro là WAV tổng hợp qua Web Audio; STT và chat được
thay tạm bằng kết quả kiểm soát để tách lỗi UI khỏi hạn mức LLM.

Kết quả: trong callback sau VAD onset của lượt nói chen, audio đã pause,
không còn `src`, capture vẫn hoạt động. WAV câu mới 116780 byte được gửi
ở lượt tiếp theo; không phải chờ hết câu mới dừng TTS. Đã phục hồi fetch,
micro và trang gốc sau khi thử; demo cuối dùng dịch vụ thật.

Đây không phải kiểm tra tiếng vọng qua loa/micro vật lý. AEC của trình duyệt
và VAD chưa xác định người nói; vẫn cần đo trong phòng thật với quạt/loa,
khoảng cách micro và âm lượng thực tế. Không khẳng định đạt mọi môi trường.

## Kiểm tra tự động

- Frontend: **52/52** test, gồm ngắt trước khi WAV sẵn sàng, hủy ticket
  đang chờ, bỏ callback cũ, spike âm lượng không ngắt, kết thúc tự nhiên,
  dừng bằng nút, lỗi micro và ngắt giọng dự phòng trình duyệt.
- Backend: **33/33** test của `test_azure_stt.py`,
  `test_azure_speech_services.py`, `test_whisper_api.py`.
- Build frontend thành công. Model 3D vẫn có cảnh báo chunk lớn; jsdom
  vẫn báo các API canvas/media không triển khai. Không phải lỗi kiểm tra
  VAD trong trình duyệt thực.
- Test route STT được tách khỏi provider/debug flag trong `.env` máy demo.
  Không đổi cấu hình hoạt động để ép test qua.

## Vấn đề còn lại và hướng tiếp theo

Thử câu text “Có loại thịt nào ăn lẩu không?” trực tiếp trên demo gặp lỗi
dịch vụ. Log xác nhận Groq `rate_limit_exceeded`, nên chưa đo được độ trễ
LLM hay đánh giá nội dung tư vấn ở lượt này. Không thay bằng câu dựng sẵn
để báo tư vấn đã thành công.

Giảm độ trễ sâu hơn cần STT continuous/streaming có partial/final riêng,
giữ âm thanh liên tục khi ngắt lời; LLM stream từng câu sau khi kiểm tra
grounding rồi nối các đoạn TTS theo thứ tự. Cần bổ sung kiểm tra hủy giữa
chừng, sửa partial, mất mạng và giữ phần sửa ý trước khi bật mặc định.
Trong bản hiện tại, cải thiện đã triển khai là ngắt lời, giữ listener ấm,
hủy công việc phát thừa và tái sử dụng kết nối STT.

## Bằng chứng và cách chạy lại

- `artifacts/voice_latency_20261115.json`
- `artifacts/barge_in_browser_20261115.json`
- `artifacts/ui_desktop_20261115.jpg`
- `artifacts/ui_demo_20261115.jpg`
- `python -m evaluation.audit_voice_latency` (cần backend localhost và
  cấu hình Azure sẵn có; dùng audio tổng hợp đã lưu).
- Probe trình duyệt: `apps/kiosk-airi/evaluation/barge-in-probe.js` chỉ
  import thủ công khi phát triển. Truyền URL WAV tổng hợp do Vite phục vụ,
  bấm mic, lưu `window.__bargeProbe.snapshot()`, dừng mic, gọi `cleanup()`
  rồi reload. Không import probe vào app sản phẩm.
