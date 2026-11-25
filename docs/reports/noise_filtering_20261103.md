# Báo cáo lọc nhiễu và nhận diện giọng nói — 03/11/2026

Phản ánh của người dùng: chưa nói nhưng quạt, điều hòa hoặc máy tính làm demo tưởng có lời
nói; khi nói thì nhận sai. Chưa xác minh được nguyên nhân trên chính chiếc mic của người dùng.

## Thay đổi đã áp dụng

- Nâng Silero VAD v5 lên **v6** ở cả browser và server; giữ frame 512 mẫu/context 64 mẫu.
- Microphone tiếp tục yêu cầu echo cancellation, noise suppression và automatic gain control;
  thêm hint mono, 48 kHz, 16-bit và voice isolation. Browser có thể bỏ qua hint không hỗ trợ.
- Whisper chính vẫn là **whisper-large-v3**, dự phòng **whisper-large-v3-turbo** khi request lỗi.
- Giữ audio gốc cho Whisper, chỉ dùng khuếch đại có giới hạn trong bước phát hiện tiếng nói nhỏ.
- Gỡ package thử nghiệm denoiser khỏi dependency của kiosk; công cụ replay dùng cài đặt tùy
  chọn trong thư mục nghiên cứu riêng.

Pipeline: mic → xử lý AEC/NS/AGC của browser → Silero v6 trong browser → WAV mono 16 kHz →
Silero v6 phía server + bộ kiểm tra PCM/độ tin cậy STT → Whisper → hội thoại.

VAD quyết định có lời nói hay không, không phải bộ khử nhiễu âm thanh. Việc nâng VAD không
chứng minh rằng tiếng quạt đã bị loại khỏi bản ghi hoặc mọi lời nói đều được nhận đúng.

## Kiểm tra endpoint sau restart

| Đầu vào | Kết quả |
|---|---|
| Nhiễu quạt mô phỏng 20 giây, mức thấp và biến thiên gain | Không phát hiện tiếng nói; transcript rỗng |
| Nhiễu quạt mô phỏng 20 giây, mức cao | Không phát hiện tiếng nói; transcript rỗng |
| Tiếng Việt nhỏ trộn nhiễu quạt mô phỏng | Có phát hiện tiếng nói và trả transcript |
| Mẫu tiếng Anh sạch | Trả đúng “Concord returned to its place amidst the tents.” |

Không chấm mẫu VI trộn quạt là hoàn toàn đúng nếu chưa đối chiếu reference; endpoint artifact
chứng minh mẫu này đi qua VAD và được giải mã. Hai mẫu nhiễu không đến bước trả lời khách.
Đây là upload file vào endpoint, không phải phép đo mic vật lý hoặc browser AEC.

Frontend, backend health và model VAD được phục vụ đều HTTP 200. SHA-256 model browser và
server trùng nhau: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

Bằng chứng: [endpoint delivery](artifacts/noise_delivery_20261103.json).

## So sánh bộ lọc với Whisper thật

152 request đều dùng `whisper-large-v3`, HTTP 200. Chẩn đoán nhỏ gồm 4 câu VI (89 từ
tham chiếu) và 4 câu EN (126 từ), mỗi ngôn ngữ ở 4 điều kiện; có thêm mẫu chỉ nhiễu.
Các câu và nhiễu được dùng lại giữa các nhánh để so sánh cặp. WER tính tổng lỗi chia tổng
từ tham chiếu; thấp hơn là tốt hơn. Đây là tập đã dùng trong phát triển, không phải đánh
giá mù hoặc tỷ lệ lỗi ngoài thực tế.

| Ngôn ngữ / điều kiện | Gốc | RNNoise | GTCRN | DeepFilterNet3 |
|---|---:|---:|---:|---:|
| VI sạch | 4,49% | 4,49% | 4,49% | 4,49% |
| VI máy hút bụi 0 dB | 22,47% | 39,33% | 28,09% | 44,94% |
| VI máy hút bụi 10 dB | 5,62% | 8,99% | 6,74% | 10,11% |
| VI bàn phím 0 dB | 4,49% | 5,62% | 4,49% | 5,62% |
| EN sạch | 0% | 0% | 0% | 0% |
| EN máy hút bụi 0 dB | 0,79% | 1,59% | 3,17% | 0,79% |
| EN máy hút bụi 10 dB | 0% | 0% | 0,79% | 0% |
| EN bàn phím 0 dB | 0% | 0% | 0% | 0% |

RNNoise làm xấu 7/16 dòng VI, GTCRN 4/16, DeepFilterNet3 7/16. Vì vậy chưa bật các bộ
lọc này mặc định. Âm thanh nghe sạch hơn có thể làm mất chi tiết hữu ích cho Whisper.
Đây chưa đủ để kết luận denoiser nào tốt nhất nói chung; lựa chọn còn phụ thuộc model,
mức lọc và mic. DeepFilterNet được thử bằng binary Windows release v0.5.6; RNNoise/GTCRN
bằng worklet/WASM trong package `@sapphi-red/web-noise-suppressor@0.4.1`, không phải
chạy mọi cấu hình của từng nghiên cứu.

VAD v6 giảm số control được kích hoạt từ 7 xuống 6 so với v5 trong phép so sánh server
cùng audio; cả hai không bỏ sót mẫu speech của phép đo nhỏ đó. TEN VAD cũng được thử
nhưng kích hoạt trên nhiều control môi trường và có điều khoản giấy phép bổ sung,
nên chưa tích hợp. Một số control vẫn kích hoạt VAD: chưa thể tuyên bố hết nhận nhầm.

## Paper và hướng dẫn đã đọc

- Valin, *A Hybrid DSP/Deep Learning Approach to Real-Time Full-Band Speech Enhancement*
  (RNNoise): [paper](https://arxiv.org/abs/1709.08243),
  [mã nguồn](https://github.com/xiph/rnnoise). Kết hợp DSP với mạng nhỏ, thích hợp độ trễ thấp.
- Schröter et al., *DeepFilterNet: Perceptually Motivated Real-Time Speech Enhancement*:
  [paper](https://arxiv.org/abs/2305.08227),
  [mã nguồn](https://github.com/Rikorose/DeepFilterNet). Đánh giá tăng cường âm thanh không
  thay thế phép đo WER của ứng dụng.
- *GTCRN: A Speech Enhancement Model Requiring Ultralow Computational Resources*, ICASSP 2024:
  [paper](https://ieeexplore.ieee.org/document/10448310),
  [mã nguồn](https://github.com/Xiaobin-Rong/gtcrn). Tài nguyên thấp hữu ích cho client;
  cần kiểm tra biến dạng tiếng nói khi ghép với Whisper.
- [Silero VAD](https://github.com/snakers4/silero-vad):
  dùng model phát hiện speech thay vì chỉ ngưỡng âm lượng.
- [TEN VAD](https://github.com/TEN-framework/ten-vad): ứng viên phát hiện speech, chưa triển khai.
- [MediaTrackConstraints](https://developer.mozilla.org/en-US/docs/Web/API/MediaTrackConstraints):
  yêu cầu browser AEC/NS/AGC; cần phân biệt constraint yêu cầu với setting thực tế.

Nguồn âm: [FLEURS](https://huggingface.co/datasets/google/fleurs),
LibriSpeech và control ESC-50 theo manifest cục bộ. Mẫu fan-like được sinh tổng hợp;
máy hút bụi/bàn phím không tương đương bản ghi quạt/điều hòa thật của người dùng.

## Kiểm thử và giới hạn

Backend noise/speech/STT/API: 526 tests pass; frontend: 115 tests pass; production build pass.
Ruff, Python compile và diff check pass. Kết quả kiểm thử tự động không chứng minh chất lượng
mic vật lý. Test tiếng Việt nhỏ có thể skip nếu máy khác chưa có cache âm thanh FLEURS.

Không có bản ghi mic thực tế của người dùng trong đợt này, chưa đo end-to-end browser
AEC/NS/AGC trong phòng thật. VN/EN/ZH vẫn ép language của Whisper; nói EN khi đang chọn VN
có thể sai. LiveKit chưa tích hợp; đây là pipeline HTTP STT và ticket-stream TTS.

Bước đánh giá tiếp theo cần dữ liệu thiết bị thật: ghi riêng tiếng quạt/điều hòa khi im lặng,
câu nói VI/EN, và câu nói khi đang phát TTS; lưu WAV trước STT và setting mic thực tế.
So sánh false activation mỗi phút, WER/CER, mất âm đầu/cuối và độ trễ trên cùng audio.
Chỉ bật thêm denoiser nếu cải thiện cả nhận nhầm lẫn transcript mà không cắt tiếng nói nhỏ.

## Artifact và tái lập

- [Tổng hợp WER](artifacts/noise_summary_20261103.json)
- [STT ablation](artifacts/noise_stt_ablation_20261103.json)
- [DeepFilterNet STT](artifacts/noise_deepfilter_stt_20261103.json)
- [VAD v5/v6](artifacts/noise_silero_model_ablation_20261103.json)
- [TEN VAD](artifacts/noise_ten_candidate_20261103.json)
- [Worklet replay](artifacts/noise_worklet_replay_20261103.json)
- [Server tests](artifacts/noise_backend_final_20261103.xml)

Các công cụ trong `evaluation/prepare_noise_ablation.py`,
`evaluation/replay_noise_worklets.mjs`, `evaluation/audit_noise_ablation.py`,
`evaluation/audit_noise_models.py` và `evaluation/audit_ten_noise.py` dùng cache nguồn trong
`.tmp`. Đây là công cụ nghiên cứu có dependency tùy chọn và cần dữ liệu/audio phù hợp;
không nằm trong luồng chạy kiosk. Hướng dẫn cài package worklet nghiên cứu nằm đầu script.

