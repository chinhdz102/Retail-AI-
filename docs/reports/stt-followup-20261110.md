# Kiểm tra tiếp luồng STT và khôi phục dữ liệu — 10/11/2026

Demo gọi backend của dự án trong worktree `vietnamese-whisper-stt`, cổng 8000.
Log của những lượt người dùng trước khi sửa có `/audio/transcribe`, tiếp theo
`/agent/respond` và TTS. Giao diện không gọi bộ nhận diện chữ của trình duyệt.
Backend đã khởi động lại với phiên bản `vi-whisper-20261110-3`.

`GET /audio/config` xác nhận Groq, `whisper-large-v3`, `language=vi`, không prompt,
Silero v6 + YAMNet và nguồn sản phẩm `snapshot`. Multipart đi tới Groq chỉ có
file, model, language, temperature=0, verbose_json và yêu cầu timestamp đoạn.
Không gửi instruction, lịch sử chat hay từ vựng sản phẩm. Mỗi lượt trả request_id
và pipeline_version; log mới chỉ ghi số liệu và lý do, không ghi lời nói.

## Lỗi tìm thấy và thay đổi

- Bộ thu báo đang nghe ngay khi AudioContext chạy, trước khi nhận được frame
  âm thanh. Đã chờ frame đầu tiên và tránh ghi đè trạng thái đang ghi âm.
- Model được tạo lại mỗi lượt. Đã giữ sẵn trọng số, còn trạng thái RNN, mic và
  AudioContext thuộc riêng từng lượt và vẫn đóng khi gửi STT/phát TTS.
- Bộ phát hiện ở browser không có bước phục hồi âm lượng nhỏ, trong khi backend
  có. Đã bật AGC của thiết bị và phân tích song song tín hiệu gốc/tín hiệu nhỏ
  tăng âm lượng bằng hai trạng thái Silero độc lập; WAV vẫn giữ mẫu âm thanh gốc.
  Ngưỡng Silero là 0,5 và tối thiểu 160 ms để giữ câu ngắn.
- Backend chỉ tăng mức âm cho VAD, rồi gửi bản rất nhỏ nguyên gốc tới Whisper.
  Đã điều chỉnh file nhỏ sau xác minh có lời nói, giới hạn gain 32 và peak 0,95.
  Trên WAV browser được giữ nguyên để so sánh, các bản nhỏ của “Không”, “Dạ”,
  “Một lít”, “Không đường” đổi từ sai thành đúng sau bước này.
- Code cũ từ chối cả câu khi một chỉ số Whisper thấp. Điều kiện không có lời
  nói đã đổi sang kết hợp no_speech_prob và avg_logprob như mã nguồn Whisper.
  Timestamp vượt audio được báo là câu không căn chỉnh được, thay vì lỗi mạng.
- App âm thầm bỏ câu bị từ chối. Đã thông báo chưa nghe rõ và tiếp tục nghe;
  callback cũ sau khi dừng không được thay đổi thông báo của lượt mới.
- Silero có thể kích hoạt trên tiếng ho/động vật. Bài thử file gốc đã bỏ sót lỗi
  xuất hiện trên WAV browser cắt: tiếng ho thành “Cơm cơm”, tiếng bò thành câu
  giới thiệu kênh. Đã bổ sung xác minh sự kiện âm thanh trước Whisper bằng
  YAMNet; không chặn câu chữ cụ thể và không sửa chữ bằng LLM.

YAMNet chỉ phủ quyết khi âm khác lời nói có điểm ít nhất 0,9 và bằng chứng lời
nói chưa đạt 0,95; im lặng không được dùng làm sự kiện cạnh tranh. Đây là chính
sách thận trọng đã kiểm tra trên tập công khai nhỏ, không phải tỷ lệ lỗi đã
được chứng minh ngoài thực tế. Thử chỉ lấy điểm lời nói cao nhất hoặc điểm
trung bình vẫn bỏ lọt tiếng ho; không dùng hai cách đó làm luật chấp nhận.
Model ONNX 16 MB có frontend và license kèm theo, được ghim phiên bản/hash;
runtime không tự tải model và không bỏ qua bước này khi model lỗi.

## Kết quả thực tế

136 file đi qua HTTP API đang chạy và Whisper thật; tất cả HTTP 200.
Các nhãn tham chiếu chỉ dùng chấm kết quả, không đi vào request.

| Nhóm | Số mẫu | Có chữ | Chữ rỗng | Lỗi API |
| --- | ---: | ---: | ---: | ---: |
| 50 loại âm thanh môi trường ESC-50 | 50 | 0 | 50 | 0 |
| Im lặng, white noise, sóng 440 Hz | 3 | 0 | 3 | 0 |
| WAV tiếng ho/bò do browser cắt | 2 | 0 | 2 | 0 |
| Giọng người VietSuperSpeech | 24 | 24 | 0 | 0 |
| Giọng người FLEURS tiếng Việt | 12 | 12 | 0 | 0 |
| VietSuperSpeech dev kiểm tra thêm | 24 | 24 | 0 | 0 |
| Từ/câu ngắn tổng hợp, file gốc | 7 | 7 | 0 | 0 |
| Cùng 7 câu qua browser, mức thường và nhỏ 20 lần | 14 | 13 | 1 | 0 |

“Có chữ” không đồng nghĩa nhận đúng. 14 mẫu browser chỉ có **10/14 đúng toàn
câu**: “Có” vẫn bị bỏ hoặc thành “Đó”; “Sữa” thành “Sơ”. 7 file gốc chỉ 2/7
đúng toàn câu. Câu nói thật “Đăng ký kênh” được giữ ở cả file gốc và hai mức âm
qua browser; không có danh sách chặn câu này.

FLEURS 12 mẫu có WER 7,41% theo chuẩn hóa hiện có. VietSuperSpeech/dev có nhãn
tự động và không phải bản chép được người kiểm duyệt; không dùng số của hai
nhóm đó làm kết luận độ chính xác thực tế. Thử cùng 7 file ngắn với turbo được
5/7 đúng, nhưng cũng nhận tiếng ho/bò thành chữ. Chưa có đối chiếu turbo đầy đủ
trên giọng người nên không tự đổi model hay thêm fallback.

Kiểm tra Chrome chạy WASM/AudioWorklet thật, nguồn âm tổng hợp không dùng mic
vật lý. Bài đầu dùng hai AudioContext làm nguồn/capture có thể cắt mất đầu âm,
nên không dùng nó để kết luận tỷ lệ cải thiện. Bài xác minh dùng cùng clock:
14/14 câu được thu, giữ tối thiểu 99,99997% năng lượng nguồn và hệ số tương
quan tối thiểu 0,9999977. Thêm ba bài kiểm tra thiết bị giả xác nhận im lặng
không upload, speech chỉ upload một WAV sau khi dừng track và hủy mở mic không
tự sống lại. Chưa kiểm chứng mic, loa, âm phòng của người dùng.

538 bài backend, 40 bài frontend, build/typecheck, ruff phần thay đổi và kiểm
tra vòng đời Chrome đều qua. Bộ backend dùng catalog demo cho fixture cũ;
snapshot có kiểm tra riêng về dữ liệu thật, giá, lookup và quarantine.

## Dữ liệu GitHub đang được dùng

Đã fetch `origin` và restore riêng `data/` từ `origin/main`, commit
`ce8d3b51e73110ee437a734b3ff98f95f447bb13`. So sánh cuối không có khác biệt dữ
liệu với GitHub. JSON có 3.099 dòng hợp lệ về cấu trúc; 18 dòng giá sale lớn
hơn giá gốc bị loại theo quy tắc importer hiện có, còn 3.081 sản phẩm.

SHA-256 file sản phẩm:
`a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`.

Demo trước đó dùng catalog mẫu mặc định, không đọc file đầy đủ. Đã thêm chế độ
snapshot và bật trong `.env` cục bộ để đọc trực tiếp file GitHub, giữ ngày dữ
liệu và không nhập database. API trả `TLM_10005885`, bánh bao Thọ Phát 250 g,
giá 30.700đ đúng file nguồn. Đây là giá/tồn kho ghi nhận trong snapshot.

## Bằng chứng và chạy lại

- [136 lượt API thật](artifacts/voice_followup_live_20261110.json)
- [Browser thu 14 mẫu và hai âm gây nhiễu](artifacts/browser_capture_clock_verified_20261110.json)
- [Kiểm tra tính toàn vẹn WAV](artifacts/browser_capture_integrity_20261110.json)
- [Vòng đời Chrome](artifacts/browser_lifecycle_followup_20261110.json)
- [So sánh hai model Whisper](artifacts/whisper_models_comparison_20261110.json)
- [So sánh mức âm trước/sau](artifacts/whisper_browser_level_recheck_20261110.json)

```powershell
uv run python -m evaluation.audit_voice_followup --source-root <checkout-chua-corpus> --capture-report docs/reports/artifacts/browser_capture_clock_verified_20261110.json --output <bao-cao-moi.json>
```

Script cần các cache công khai được liệt kê trong manifest và WAV capture đúng
hash trong `.tmp/browser-qa/captures`. Có gọi dịch vụ STT thật. Không mở mic.

## Nguồn nghiên cứu

- [Whisper large-v3: model card và giới hạn ảo giác](https://huggingface.co/openai/whisper-large-v3)
- [Logic no_speech/logprob của Whisper](https://raw.githubusercontent.com/openai/whisper/main/whisper/transcribe.py)
- [Groq: ngôn ngữ, temperature, định dạng âm thanh và prompt](https://console.groq.com/docs/speech-to-text)
- [Google YAMNet](https://github.com/tensorflow/models/tree/master/research/audioset/yamnet)
- [Bản chuyển ONNX có nguồn gốc và license](https://huggingface.co/audiomagic/yamnet-onnx)
- [PhoWhisper tiếng Việt](https://huggingface.co/vinai/PhoWhisper-small)

Các model nhỏ tiếng Việt là ứng viên để đo tiếp, không được thay vào demo chỉ
theo model card. TTS Azure hiện có được giữ nguyên trong lượt sửa này. Mic vẫn
đóng khi STT, trợ lý hoặc TTS đang xử lý và chờ 400 ms sau loa; chưa hỗ trợ nói
chen khi Sky đang trả lời. TV/người khác nói rõ trong phòng vẫn có thể được nhận
là lời nói; các bước trên không xác định người đang nói với kiosk.
