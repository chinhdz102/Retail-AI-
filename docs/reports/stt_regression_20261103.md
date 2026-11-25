# Chẩn đoán nhận diện tiếng Việt và tiếng Anh — 03/11/2026

Runtime đang dùng **Groq `whisper-large-v3` chính**, **`whisper-large-v3-turbo` dự
phòng**. Đã xác nhận bằng request HTTP thật trước và sau restart. Trình duyệt đang
thu âm bằng Silero/PCM, không dùng Web Speech Recognition cho luồng mặc định.
Chưa đủ bằng chứng xác định nguyên nhân của toàn bộ các lượt micro người dùng nhận sai.

## Những gì đã xác minh

1. `/audio/transcribe` ép ngôn ngữ theo lựa chọn VN/EN/ZH trên giao diện. Cùng file
   tiếng Anh, gửi `language=en` nhận đúng, ép `language=vi` sinh câu tiếng Việt sai
   nội dung. Bốn mẫu ép VI đều bị quality guard loại trước khi đến agent. Người dùng
   không nhớ locale khi lỗi, nên đây là lỗi có thể tái hiện, chưa phải kết luận cho
   các lượt họ đã gặp.
2. Có lỗi hậu xử lý catalog làm biến dạng chữ Whisper đã nhận. Một clip ViVoice34 có
   “Liên hiệp quốc dân, kỷ luật, mùa màng khá” bị đổi “quốc dân” thành “Jordan”.
   Nguyên nhân là bỏ dấu khiến `mùa`, `múa`, `mưa` thành cue `mua`; cue từ câu khác
   cũng có thể kích hoạt sửa brand. Đây là lỗi phần mềm đã tái hiện và sửa trong đợt này.
3. Whisper Large vẫn nhận sai một số tiếng Việt vùng miền dù `avg_logprob` tốt.
   Ví dụ ViMD `11_0309` vẫn biến “mô hình cây rong riềng” thành “mô hình dòng diệt”.
   VAD xác minh có tiếng nói, không xác minh lời nhận ra đúng hay xác định người nói.
4. So cùng hash audio với artifact 02/11: 12/12 FLEURS và 3/3 ViVoice còn nằm trong
   giới hạn server trả transcript giống hệt trước. Ba ViMD cũng giữ lỗi như các lần
   đo trước ngày 03/11. Chưa thấy decoder xuống chất lượng trên các file này. Bốn
   ViVoice dài hơn 60 s nay bị giới hạn input trả 422; ghi riêng, không tính thành
   WER thấp hơn hoặc đoạn không có lời. Chúng cũng vượt cap frontend 12 s hiện có.

## Thay đổi đã giao

- Xác nhận cue thương mại bằng từ có dấu; vẫn chấp nhận dạng thiếu dấu, nhưng từ có
  dấu khác nghĩa không được coi là cue tương đương. Không thêm bảng sửa riêng câu
  “quốc dân”, brand Jordan hoặc câu test nào vào implementation.
- Không lấy cue ở câu khác qua `.`, `!`, `?`, `;` hoặc xuống dòng để fuzzy sửa brand.
- Đồng bộ `.env.example`: Large v3 chính, Turbo dự phòng. File cấu hình mẫu trước đó
  ghi thứ tự ngược với runtime; `.env` đang chạy không có override model STT.
- Bổ sung script đo cùng audio qua endpoint/service và script ablation ngôn ngữ/prompt.
- Restart backend bằng `scripts/start_demo.ps1 -RestartBackend`, giữ flags demo hiện có.

## Diagnostic người thật

Artifact [paired baseline](artifacts/stt_regression_paired_20261103.json) ghi 26 file
qua endpoint thật và `GroqService`; có raw text, corrected text, model, quality,
latency, fallback trace và SHA-256 audio. Audio công khai chỉ cache trong `.tmp`.

| Nhóm | File gửi | HTTP 200 được chấm | WER tổng hợp endpoint | CER tổng hợp |
| --- | ---: | ---: | ---: | ---: |
| FLEURS vi_vn test | 12 | 12 | 7,41% | 4,43% |
| ViVoice34 preview | 7 | 3 | 23,04% | 12,58% |
| ViMD test preview | 3 | 3 | 13,46% | 12,82% |
| LibriSpeech test-clean | 4 | 4 | 0% | 0% |

WER tổng hợp = tổng lỗi từ / tổng từ tham chiếu; không phải trung bình WER từng câu.
Các số trên là baseline trước sửa hậu xử lý. 22 response HTTP 200 đều báo model
`whisper-large-v3`. Ở lượt service riêng có một fallback sang Turbo sau provider
error và nhiều lượt đổi credential sau rate limit; không gộp chúng thành đối chứng
chỉ thay một yếu tố. Bốn file 422 do dài hơn 60 s không có score.

Nguồn: [FLEURS](https://huggingface.co/datasets/google/fleurs), CC-BY-4.0;
[ViVoice34](https://huggingface.co/datasets/anonymous-vivoice34/ViVoice34), CC-BY-4.0;
[ViMD](https://huggingface.co/datasets/nguyendv02/ViMD_Dataset), CC-BY-NC-ND-4.0;
[LibriSpeech](https://huggingface.co/datasets/openslr/librispeech_asr), CC-BY-4.0.
LibriSpeech lấy bốn hàng đầu test-clean trước khi chạy ASR, cùng một speaker; không
đại diện tiếng Anh có giọng Việt. Lần tải rộng hơn gặp dataset-server 502 nên giới
hạn về bốn hàng đầu đã tải được; không chọn mẫu theo kết quả nhận diện.
ViVoice province không phải nhãn giọng đã được người nghe xác minh. ViMD chỉ có
ba clip miền Bắc đã lộ trong đợt trước. Đây không phải benchmark mù hay điểm micro kiosk.

## Vì sao chưa bật auto language mặc định

[Ablation 26 lượt](artifacts/stt_language_bias_20261103.json) dùng 7 file Việt và
4 file Anh trên cùng Large v3:

- Ép đúng ngôn ngữ + prompt hiện tại: bốn file Anh WER 0%; các file Việt giữ lỗi đã có.
- Bỏ cả ngôn ngữ và prompt: bốn file Anh vẫn 0%, nhưng hai file Việt kém hơn:
  một file FLEURS từ 12,5% lên 18,75%; ViMD `12_0284` từ 0% lên 15%.
- Ép VI trên tiếng Anh: cả bốn sinh transcript sai và bị quality guard loại.

[Ablation giữ prompt VI nhưng bỏ ép ngôn ngữ](artifacts/stt_auto_context_20261103.json)
giữ WER trên bảy file Việt, nhưng ba file Anh còn lỗi và một file bị loại. Vì vậy
chưa đủ bằng chứng thay mặc định bằng một trong hai cách auto này. Ngôn ngữ UI,
prompt bias và language detection cần tách riêng khi thiết kế chế độ đa ngôn ngữ.
Metadata decoder không phải xác suất đúng đã hiệu chuẩn; không dùng để tuyên bố
transcript tự tin là transcript đúng.

## Xác nhận bản sửa

- Sáu regression mới đều fail trước sửa; tổng [23 test catalog sau sửa](artifacts/stt_catalog_preservation_green_20261103.xml)
  qua, giữ các test sửa tên hàng/variant có ngữ cảnh đã có.
- [Replay offline 22 raw transcript](artifacts/stt_catalog_replay_20261103.json):
  một output thay đổi, WER clip Hải Phòng giảm 5,23% → 3,92%; không output nào có
  WER tăng. Đây là replay hậu xử lý, không phải một lần decoder mới.
- [498 test backend catalog/Groq/API](artifacts/stt_regression_scoped_20261103.xml)
  qua, không failure. Chỉ chạy bộ liên quan, không gọi đây là full backend.
- [115 test frontend](artifacts/stt_regression_frontend_20261103.json) qua; build
  production qua. Còn warning chunk model-viewer lớn từ trước và giới hạn canvas/media
  của jsdom; chúng không xác nhận chất lượng mic/loa.
- Ruff cho các file sửa/script và compileall qua.
- [Delivery thật sau restart](artifacts/stt_delivery_20261103.json): frontend và
  backend health HTTP 200; file Hải Phòng giữ “quốc dân”, không còn “Jordan”; một
  file Anh nhận đúng. Cả hai trả `whisper-large-v3`.

## Phần cần kiểm tra tiếp trên thiết bị

Chưa có audio của đúng lượt micro bị sai để so với lời người dùng đã nói. Cần đối
chiếu cùng lần thu: thiết bị được chọn, locale, bản WAV gửi server, transcript gốc
Whisper và transcript sau hậu xử lý. Không thể suy micro tốt từ LibriSpeech/FLEURS.
Browser automation hiện trả lỗi kết nối khi đọc tab demo nên chưa xác minh được
micro/locale đang hiển thị của người dùng; không tự đổi thiết bị hoặc cấu hình âm thanh.

Không thay VAD, gain, noise suppression, model hoặc prompt mặc định chỉ từ giả
thuyết. Không sửa transcript sai bằng LLM đoán ý khách. Chưa nghiệm thu tiếng ồn,
echo loa, âm lượng vật lý, lẫn tiếng người khác hay nói trộn Việt/Anh. Trạng thái
production tiếp tục **NOT READY**.

Lệnh chạy lại từ root repo:

```powershell
.\.venv\Scripts\python.exe -m evaluation.audit_stt_regression --output docs/reports/artifacts/stt_regression_repeat.json
.\.venv\Scripts\python.exe -m evaluation.audit_stt_language_bias --modes selected_language auto_no_prompt force_vi --output docs/reports/artifacts/stt_language_bias_repeat.json
.\.venv\Scripts\python.exe -m evaluation.audit_stt_language_bias --modes auto_ui_prompt --output docs/reports/artifacts/stt_auto_context_repeat.json
```

Tài liệu đối chiếu: [Groq Speech-to-Text](https://console.groq.com/docs/speech-to-text)
về language, prompt và verbose metadata; [báo cáo nghiên cứu voice trước đó](voice_optimization_20261103.md)
ghi paper ViMD/Whisper hallucination/VAD cùng giới hạn áp dụng. Các kết quả paper
không được dùng làm điểm chất lượng cho demo.
