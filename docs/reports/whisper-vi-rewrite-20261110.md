# Kiểm tra và viết lại STT tiếng Việt — 10/11/2026

Worktree: `C:\Users\Chinhdz\.codex\worktrees\vietnamese-whisper-stt\Retail-AI--main`.
Nhánh: `codex/vietnamese-whisper-stt`, tách từ `origin/main` tại
`ce8d3b51e73110ee437a734b3ff98f95f447bb13`.
Thư mục nguồn có nhiều thay đổi sẵn; không reset toàn bộ thư mục nguồn.
Mang sang riêng các thành phần thoại cần thiết, gồm cách phát TTS đang dùng.

## Những chỗ có vấn đề trong code

| File/đường chạy cũ | Vấn đề | Thay đổi |
| --- | --- | --- |
| `voice-controller.ts` | Mức âm lượng/RMS kích hoạt ghi âm; âm động cũng đủ điều kiện. Có nhiều đường Browser SpeechRecognition/MediaRecorder. | Một đường AudioWorklet → Silero → WAV, giới hạn lượt, sở hữu mic theo thế hệ. |
| `groq.py` | Gợi ý từ vựng bán hàng; chỉ lấy text; text rỗng bị coi là lỗi và đổi model giải mã lại. | Bỏ hẳn STT khỏi dịch vụ chat; dịch vụ Whisper riêng, không prompt, không đổi model sau kết quả rỗng. |
| `App.vue` | Phản hồi về muộn có thể tiếp tục lượt đã dừng; mở mic ngay sau TTS; nút dừng bị khóa lúc nhận diện. | Hủy request và kiểm tra thế hệ; đóng mic khi xử lý/phát giọng; chờ 400 ms; nút dừng luôn dùng được. |
| `microphone-devices.ts` | Tự ưu tiên AMD có thể ghi đè thiết bị người dùng chọn; đầu vào ảo có thể là loopback. | Giữ lựa chọn rõ ràng; chỉ tự tránh thiết bị ảo khi chưa có lựa chọn. |
| Vòng đời MicVAD 0.0.31 | `destroy()` gọi `getAudioInstances()` khi chưa tạo đủ stream/context/node, có thể không tới bước giải phóng model. | Adapter tự sở hữu model/context/track, dọn được cả lúc khởi động bị hủy. Giữ FrameProcessor công khai của thư viện. |
| `voice-neural.ts`/Vite | Runtime WASM có đường dẫn tương đối bị viết lại thành import từ public; browser không nạp được. | URL runtime tuyệt đối; kiểm lại trên Chrome thật. |
| `App.vue`, `ChatPanel.vue`, config/TTS | Còn giữ nhánh Anh/Trung của giao diện và giọng cũ. | Xóa các nhánh này khỏi luồng thoại; API STT/TTS chỉ nhận `vi`. |

Không tìm thấy câu “đăng ký kênh” được gắn cứng vào kết quả runtime.
Đã tái hiện trên Whisper thật: audio cow sinh câu kêu gọi đăng ký kênh với
`no_speech_prob≈0.053`, `avg_logprob≈-0.136`; tiếng ho sinh “Cơm” lặp bảy lần.
Do đó độ tin cậy do riêng Whisper báo không chứng minh đã có lời nói thật.
Các nhánh đa ngôn ngữ là phần dư cần dọn; chưa có bằng chứng chúng trực tiếp gây câu này.

## Kiến trúc mới và lựa chọn model

Dùng Whisper large v3 trên Groq theo yêu cầu, cố định tiếng Việt, nhiệt độ 0,
không prompt, không từ vựng/catalog, không lịch sử transcript, không LLM sửa lời.
Browser và server kiểm tra Silero v6 độc lập. Backend dùng lại audio đã giải mã
để tạo WAV chuẩn thay vì nhiều lần chuyển định dạng.
Không thêm denoiser; tránh thay đổi tín hiệu nhiều lớp khi chưa có bằng chứng cải thiện.

Đã xem các nguồn chính:

- [Whisper large v3](https://huggingface.co/openai/whisper-large-v3): có thể cố định ngôn ngữ; model không thay thế phát hiện tiếng nói.
- [Groq STT](https://console.groq.com/docs/speech-to-text): WAV 16 kHz mono, nhiệt độ 0, verbose JSON và bằng chứng theo segment.
- [PhoWhisper small](https://huggingface.co/vinai/PhoWhisper-small): Whisper được tinh chỉnh trên 844 giờ tiếng Việt, BSD-3; cần benchmark và runtime local riêng trước khi thay model.
- [Gipformer 1.5](https://huggingface.co/g-group-ai-lab/gipformer1.5-68M-rnnt), [Gipformer](https://github.com/ggroup-ai-lab/gipformer): RNNT CPU/ONNX; nghiên cứu tham khảo, không nối vào luồng Whisper mới.
- [Zipformer 30M](https://huggingface.co/hynt/Zipformer-30M-RNNT-6000h): giấy phép CC-BY-NC-ND, không chọn làm mặc định cho sản phẩm bán lẻ.
- [Parakeet tiếng Việt](https://huggingface.co/nvidia/parakeet-ctc-0.6b-Vietnamese): lớn hơn và đòi hỏi thêm triển khai; không thêm một đường dự phòng tùy tiện.

TTS tiếng Việt giữ Azure Hoài My và cách đọc số/đơn vị hiện có.
API phát giọng và giao diện không còn lựa chọn Anh/Trung.
Backend hội thoại sản phẩm chung còn hỗ trợ văn bản đa ngôn ngữ; nó không chọn ngôn ngữ STT/TTS.

## Kiểm thử và giới hạn thực tế

- 526 kiểm thử backend qua; 40 kiểm thử frontend qua; TypeScript và bản build qua; kiểm tra lint qua.
- Chrome thật với thiết bị giả: im lặng 5 giây có xử lý frame nhưng không upload;
  speech tạo đúng một WAV; track đã dừng trước callback upload;
  dừng lúc khởi động không mở lại mic; AudioContext và track đều đóng.
- Replay API + Whisper thật: 50 nhóm âm thanh môi trường, 24 clip người nói tiếng Việt,
  7 clip TTS ngắn và 3 đối chứng tổng hợp (84 ca).
- 50 âm thanh môi trường và 3 đối chứng tổng hợp trả text rỗng.
  Đây không phải tỷ lệ false positive ngoài đời: nhãn môi trường không xác nhận độc lập
  rằng file không có giọng người nền.
- Một clip người nói gặp lỗi provider 502; lần kiểm lại riêng thành công.
  Clip lặp tự nhiên ban đầu bị loại; đã sửa điều kiện lặp để không bỏ cả câu vì một cụm nhỏ lặp lại.
- Kết quả cuối sau chỉnh ngưỡng dựa trên bằng chứng đã lưu được ghi riêng là replay,
  không trình bày như một đợt gọi cloud mới. Đã gọi lại riêng clip lỗi provider và câu ngắn.

**Còn lỗi nhận từ đơn của model.** Trên audio TTS ngắn, “Có” có lúc thành “Đó”,
“Không” thành “Thông”, “Dạ” thành “Yeah”, “Một lít” thành “Một lý”, “Sữa” thành “Sơ”.
Gửi MP3 gốc trực tiếp vẫn tái hiện các lỗi tương tự (“Không” nhận đúng ở lần trực tiếp).
Vì vậy chưa có bằng chứng lỗi này do phần đổi WAV; cũng không sửa nó bằng thay từ trong transcript.
Việc tiếng nói không bị VAD bỏ không có nghĩa từ được nhận đúng.

Không có benchmark mic/loa vật lý tại kiosk. Khoảng chờ 400 ms và loại trừ mic/TTS
được kiểm bằng vòng đời; chưa đo tiếng vang phòng thật.
Không khẳng định đã loại hết hallucination hoặc tiếng người khác trong môi trường.

## Bằng chứng

- `artifacts/stt_interference_live_20261110.json`: lỗi thật ở đường cũ.
- `artifacts/whisper_vi_final_20261110.json`: 84 lượt API/Whisper; giữ cả lỗi provider.
- `artifacts/whisper_vi_final_recheck_20261110.json`: replay bằng chứng và hai lượt kiểm lại.
- `artifacts/whisper_short_raw_mp3_20261110.json`: MP3 gốc gửi thẳng provider.
- `artifacts/browser_whisper_capture_20261110.json`: Chrome/WASM/AudioWorklet thật, thiết bị giả.
- `tests/test_whisper_stt.py`, `test_whisper_api.py`, `test_speech_worker_lifecycle.py`:
  silence không gọi provider, bằng chứng sai, timeout/hủy, không giải mã dự phòng,
  chỗ worker được giữ tới khi native chạy xong.
- `App.voice.test.ts`, `voice-controller.test.ts`: mic/TTS, dừng, phản hồi cũ,
  hết phiên, giới hạn khởi động, lỗi neural và kết thúc playback cũ.
