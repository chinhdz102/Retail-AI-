# Luồng thoại tiếng Việt

Luồng trong nhánh `codex/vietnamese-whisper-stt` chỉ nhận tiếng Việt. Demo hiện
dùng `STT_PROVIDER=azure`; `groq` vẫn chọn được khi cấu hình. `/audio/config`
là nguồn xác định nhà cung cấp/model thực sự đang chạy, không suy từ tên nhánh.

## Thu âm và STT

1. Người dùng bấm bật mic. Chỉ một lượt thu được sở hữu thiết bị.
   Chỉ báo đang nghe sau khi đã xử lý được frame đầu tiên. Model được giữ sẵn
   giữa các lượt; track mic và AudioContext vẫn đóng khi kết thúc lượt.
2. AudioContext chuyển đầu vào sang mono 16 kHz. AudioWorklet gom 512 mẫu;
   Silero v6 nhận diện tiếng nói, giữ 640 ms trước lời nói và kết thúc sau
   1440 ms không có bằng chứng lời nói đủ mạnh. Ngưỡng bắt đầu 0,5, ngưỡng
   kết thúc 0,35; tối thiểu 160 ms để giữ câu trả lời ngắn.
   Browser SpeechRecognition, MediaRecorder và bộ kích hoạt bằng RMS đã được bỏ.
   Mic bật chống vọng/khử ồn của trình duyệt, tắt tự điều chỉnh âm lượng (AGC).
   Không thêm hard gate hoặc bộ khử ồn chồng lớp. Silero có hai trạng thái phân tích độc lập
   cho tín hiệu gốc và tín hiệu nhỏ được tăng âm lượng; WAV giữ nguyên mẫu thu.
   Hàng đợi tối đa 64 frame (2048 ms); quá tải báo lỗi thay vì âm thầm bỏ frame.
3. Sau 16 frame (512 ms) im lặng rõ (xác suất lời nói < 0,1), gửi một WAV
   tạm để nhận diện trong khi vẫn thu nguyên tín hiệu. Không đưa chữ tạm lên
   giao diện hoặc vào lịch sử/trợ lý. Bất kỳ frame có xác suất >= 0,1 sẽ hủy
   kết quả tạm. Tối đa hai lần thử trong một lượt; sau đó dùng WAV đầy đủ.
   VAD vẫn đợi 1440 ms, giữ nguyên khoảng nghỉ và phần sửa ý. Khi chốt câu,
   đóng track mic rồi dùng kết quả tạm còn hợp lệ; nếu lỗi/bị từ chối thì gửi
   WAV đầy đủ. Không phải STT liên tục hoặc partial transcript.
4. Backend giải mã audio một lần, kiểm tra Silero độc lập trên chính tín hiệu đó.
   Audio không có tiếng nói trả text rỗng, không gọi dịch vụ nhận diện.
   YAMNet kiểm tra sự kiện âm thanh: chỉ loại khi một âm không phải lời nói
   đạt ít nhất 0,9 và bằng chứng lời nói trong CÙNG cửa sổ chưa đạt 0,95.
   Một tiếng động ở lúc khác không phủ quyết toàn bộ câu; im lặng là trung tính.
   Model này không xem chữ, lịch sử chat hay tên sản phẩm.
5. Một decoder được chọn trước cho mỗi lượt:
   - Azure: `conversation`, `vi-VN`, `detailed`, `profanity=raw`.
   - Groq: `whisper-large-v3`, `language=vi`, nhiệt độ 0, `verbose_json`.
   Không đưa từ vựng sản phẩm/lịch sử chat vào STT, không tự đổi decoder khi
   rỗng/bị từ chối. Sau xác minh có lời nói, tăng âm lượng file rất nhỏ,
   tối đa 32 lần và không để đỉnh tín hiệu vượt 0,95. File âm lượng bình thường
   không bị đổi mức âm lượng.
6. Whisper kiểm tra bằng chứng từng đoạn, độ lặp và lượng tiếng nói hỗ trợ kết
   quả. Azure kiểm tra trạng thái nhận diện, cấu trúc kết quả và độ lặp;
   confidence được ghi để chẩn đoán, không dùng ngưỡng chưa hiệu chỉnh để bỏ lời nhỏ.
   Đây là kiểm tra bổ sung, không phải danh sách chặn câu. Nói thật “đăng ký kênh”
   vẫn được nhận. Không dùng LLM sửa lời đã nghe.
7. Giao diện chỉ gửi text được chấp nhận sang trợ lý. Câu không rõ có thông báo
   nói lại rồi tiếp tục nghe. Không âm thầm nuốt lý do từ chối.
   `/audio/config` cho biết backend/model/phiên bản đang chạy; kết quả mỗi lượt
   có request_id, pipeline_version và thông số kiểm tra. Log không ghi lời nói.
   Khi `APP_ENV=development` và `STT_DEBUG_CAPTURE=true`, lưu đối chiếu cục bộ
   tối đa 12 lượt, dọn lượt cũ hơn 30 phút khi có lượt mới; không gửi dấu vết này
   vào prompt tư vấn.

Giới hạn: file tối đa 10 MiB, audio tối đa 60 giây, lượt thu liên tục 45 giây
(flush và gửi phần đã thu, không bỏ cả câu),
mở bộ thu tối đa 12 giây, STT tối đa 25 giây; hai lượt xử lý backend đồng thời.
Chỉ đổi khóa trên lỗi xác thực/hạn mức. Hủy yêu cầu không giải phóng chỗ của
worker native đang thực sự chạy.

Luồng kiosk này dùng HTTP WAV → STT và HTTP streaming TTS, không đi qua
LiveKit. Không gọi các bài test hiện tại là test phòng LiveKit.

## TTS và vòng đời

Giữ giọng Azure Hoài My, cách đọc số/đơn vị và streaming qua ticket dùng một lần.
ElevenLabs vẫn là lựa chọn khi chưa cấu hình Azure; lỗi Azure không tự đổi nhà cung cấp.
Trình duyệt có giọng `vi-VN` dự phòng.

Mic đóng khi đang nhận diện hoặc chờ trợ lý. Trong lúc chuẩn bị và phát TTS,
capture có AEC và VAD được mở để nhận người dùng bắt đầu nói. Silero xác nhận
ít nhất 160 ms lời nói rồi dừng audio ngay, giữ pre-roll 640 ms cho câu mới.
Không chờ STT hay kết thúc câu mới dừng Sky; spike mức âm đơn thuần không dừng TTS.
Khi TTS kết thúc tự nhiên, giữ nguyên capture đang mở để tránh khoảng chết giữa
hai lượt. Chỉ dùng chờ 400 ms ở nhánh dự phòng khi chưa có capture hoạt động.
Ngắt lời dựa vào AEC trình duyệt và VAD, chưa phân biệt được người gần mic với
tiếng người khác trong phòng. Cần kiểm tra mic/loa vật lý để đo vọng thực tế.
Backend STT Azure tái sử dụng kết nối HTTP/TLS giữa các lượt. STT tải WAV
trong khoảng nghỉ hoặc sau khi kết thúc lượt; không có partial transcript hay
streaming STT. Backend nạp sẵn Silero/YAMNet khi khởi động. Khi bật mic/bắt
đầu nói, `/audio/prepare` làm ấm kết nối TTS, gộp các yêu cầu cùng lúc và
không lặp khi kết nối còn ấm trong 45 giây. Không tạo lời chào hoặc audio giả.
Upload tạm bị hủy sẽ dừng cloud request khi backend thấy client ngắt kết nối;
worker native vẫn giữ chỗ cho tới khi thực sự chạy xong.
Dừng/đóng trang/đổi phiên hủy STT, bỏ phản hồi cũ, hủy lịch mở lại mic.
Audio của phiên hết hạn không được phát lại vào phiên mới.

Đo từng chặng STT nằm trong `quality`: `stt_acoustic_ms`, `stt_prepare_ms`,
`stt_provider_ms`, `stt_trace_ms`, `stt_total_ms`. Log tư vấn có thời gian
Plan/Composition/AdviceReview để phân biệt chậm STT, LLM hay phát giọng.
Kết quả mới: [voice_latency_and_prices_20261115.md](reports/voice_latency_and_prices_20261115.md).

## Kiểm tra

```powershell
uv run pytest -q
npm --prefix apps/kiosk-airi run test:run
npm --prefix apps/kiosk-airi run build
uv run python -m evaluation.audit_whisper_vi --source-root <checkout-chua-audio-mau> --output <report.json> --live
npm --prefix apps/kiosk-airi run test:voice-browser -- --speech-wav=<wav-16khz-mono-co-tieng-noi-va-duoi-im-lang>
```

Chrome test dùng thiết bị giả và audio có sẵn, chạy model/WASM/AudioWorklet thật.
Nó không thay thế kiểm tra mic và loa vật lý. Xem
[`whisper-vi-rewrite-20261110.md`](reports/whisper-vi-rewrite-20261110.md)
để biết kết quả và các lỗi nhận từ đơn còn tồn tại.

Đo ngắt câu, lọc ồn nhẹ và tư vấn tiếp theo:
[voice_and_advice_20261111.md](reports/voice_and_advice_20261111.md).
