# Nghiên cứu và tối ưu VAD, STT, TTS — 03/11/2026

Đợt này sửa vòng đời micro và quyền sở hữu lượt hội thoại, bổ sung kiểm tra
metadata STT, thêm Silero phía server và chuẩn hóa tiền/đơn vị cho Azure TTS.
Diagnostic dùng cả tiếng nói tự nhiên, âm môi trường và biến thể codec/âm lượng.
Chưa đủ bằng chứng nghiệm thu micro/loa thật hoặc chất lượng tương đương nhân viên.

## Kế hoạch và phạm vi

1. Đọc phương pháp, dữ liệu, giới hạn của paper gốc; đối chiếu API/version đang cài.
2. Kiểm tra các điểm chờ bất đồng bộ của micro, upload STT, agent và phát TTS.
3. Bổ sung regression theo lỗi chung, sửa và chạy lại bộ ảnh hưởng.
4. Replay Silero v5 thật; dùng dữ liệu người thật và âm môi trường có nguồn/giấy phép.
5. Kiểm tra endpoint STT/TTS thật; phân biệt accuracy, latency, lỗi transport và quota.
6. Chạy full regression/build, cập nhật demo và lưu artifact cùng hash nguồn.

Luồng thực tế là Silero v5/ONNX trên trình duyệt → WAV/MediaRecorder → giải mã
PyAV + Silero v5 phía server → Groq Whisper theo đoạn hoàn chỉnh → agent/SSE →
Azure TTS ticket/stream → browser Audio.
Không có LiveKit room, participant, audio track hoặc Agents runtime trong repo.
SSE text và MP3 trả từng chunk không biến STT thành nhận diện streaming từng từ.

## Lỗi đã sửa

| Lỗi | Hành vi sau sửa | Bằng chứng |
| --- | --- | --- |
| Factory VAD hoặc `vad.start()` của lần bật mic cũ hoàn tất sau lần mới, gọi cleanup dùng chung | Cleanup chỉ dọn session/stream của tác vụ đó; không tắt mic mới. Start và destroy của cùng VAD được nối tuần tự, destroy chỉ chạy một lần | Regression cho cả hai điểm chờ |
| Khởi tạo neural/worklet cũ thất bại rồi xoá watchdog của lần mới | Kiểm tra generation trước khi thay đổi timer/trạng thái; lần mới vẫn báo lỗi sau 12 giây nếu treo | Hai regression watchdog |
| Worklet cũ load xong làm đóng AudioContext mới | Dọn các tham chiếu cục bộ của lần khởi tạo cũ | Regression giữ context và track mới |
| AudioContext constructor ném lỗi sau khi cấp quyền mic, làm sót track | Ghi nhận stream sở hữu trước khi cấp phát context; lỗi cũng giải phóng mic | Regression track.stop |
| Khách nhập tin nhắn/đổi ngôn ngữ nhưng câu đọc cũ hoặc vé TTS đến muộn vẫn phát | Vô hiệu request phát cũ, huỷ cloud/browser playback và dữ liệu chờ đọc | Regression cloud, pending ticket, browser fallback |
| `audio.play()` từ chối autoplay muộn làm đổi câu hỏi mới từ thinking về idle | Chỉ lượt phát hiện tại được sửa trạng thái, kể cả nhánh lỗi autoplay | Regression có kết quả fail trước sửa |
| Đổi ngôn ngữ khi STT/voice agent đang chạy làm giao diện kẹt | Huỷ tác vụ cũ, bỏ callback/delta cũ, trở lại nghe bằng ngôn ngữ mới | Regression STT và agent bị huỷ |
| Voice response bị huỷ đến muộn xoá retry ID của tin nhắn mới | Chỉ chính object lượt đang sở hữu pending state được xoá nó | Regression retry giữ idempotency ID |
| ASR trả rỗng bị xem là lỗi provider rồi thử model khác | Trả transcript rỗng, `speech_detected=false`, reason `empty_transcript`; không gọi decoder tiếp | Hai regression fail trước sửa; một request thay vì hai |
| Một segment có điểm tốt che các segment thiếu metric/timestamp lỗi | Mỗi segment phải có metadata hữu hạn, khoảng thời gian hợp lệ; từ chối toàn đoạn khi không kiểm chứng được | Bảy regression missing/NaN/Inf/reversed time |
| Whisper nhận tiếng chó/chuông thành lời dù metadata trông tốt; heuristic PCM không bao phủ WebM/MP4 | Giải mã về mono 16 kHz và kiểm tra Silero v5 trước mọi decoder; không có bằng chứng lời nói thì trả transcript rỗng | Replay server, regression và API thật với ESC-50 |
| Bản ghi rất nhỏ có thể bị VAD bỏ sót | Phân tích lại với gain có giới hạn khi bản gốc âm lượng thấp; reset state/context và giữ nguyên upload gửi Whisper | Replay biến thể gain 0,02/0,05; regression không sửa input |
| CPU giải mã/VAD chạy trên event loop, hoặc huỷ awaiter làm nhả slot khi worker còn chạy | Chạy ngoài event loop; tối đa hai job, kiểm tra deadline 5 s và duration 60 s; slot chỉ nhả khi worker kết thúc | Regression cancellation/capacity, giới hạn duration và timeout |
| Tiền/khối lượng/dung tích được giao hoàn toàn cho cách đọc mặc định của TTS | Azure buffered và streaming dùng chung alias tiếng Việt cho định dạng rõ; giữ nguyên chữ hiển thị, escape XML một lần | Regression số/đơn vị/XML/SKU và Azure API giải mã được |

Không thêm bảng âm thanh/câu nói → transcript, câu trả lời hoặc SKU. Không thay
ngưỡng VAD chỉ để làm đẹp số trên tập đã lộ. Cấu hình vẫn là positive 0,55,
negative 0,35, minimum speech 250 ms, pre-roll 600 ms, redemption 900 ms, cap 12 s.

Mã chính: `apps/kiosk-airi/src/voice-controller.ts`, `App.vue`,
`backend/services/groq.py`, `speech_activity.py`, `speech_text.py` và
`azure_speech.py`. Model ONNX cùng giấy phép MIT được đóng trong wheel ở
`backend/assets/silero/`; runtime bổ sung PyAV, NumPy và ONNX Runtime CPU.
Giới hạn 5 s là deadline được kiểm tra giữa các bước, không phải hard kill native
decoder/inference. Không thay model/thuật toán của Azure hay ElevenLabs.

## Paper và quyết định kỹ thuật

Đã đọc phần phương pháp, setup và giới hạn liên quan của các nguồn dưới đây.
Đây là đối chiếu kỹ thuật, không phải tuyên bố tái lập kết quả/huấn luyện model.

- [Barański et al., ICASSP 2025 — non-speech Whisper hallucinations](https://arxiv.org/html/2501.11378v1):
  thí nghiệm có noise-only và speech ghép noise; so sánh VAD, decoder settings,
  delooping và Bag of Hallucinations. VAD là một tầng quan trọng, không bảo đảm
  hết hallucination. Áp dụng ở đây: Silero độc lập trước ASR, đo riêng negative
  controls và không retry khi ASR đã trả không có lời. Chỉ kiểm tra có speech,
  chưa cắt/ghép vùng speech như thí nghiệm trong paper; tiếng ồn trộn lời vẫn cần
  đo tiếp. Không dùng danh sách câu tiếng Anh của paper làm blacklist tiếng Việt.
- [Wang et al., Interspeech 2025 — Calm-Whisper](https://www.isca-archive.org/interspeech_2025/wang25b_interspeech.pdf):
  xác định và fine-tune một số attention head trên non-speech, đối chiếu cả tỷ lệ
  hallucination và WER trên speech. Cách này cần trọng số model; Groq hosted hiện
  không cho sửa head theo cách đó. Chưa triển khai Calm-Whisper; không gắn số giảm
  hallucination của paper cho demo. Cần giữ test speech thật khi tăng guard.
- [Dinh et al., EMNLP 2024 — ViMD](https://aclanthology.org/2024.emnlp-main.426/):
  dữ liệu phỏng vấn tự nhiên nhiều địa phương, kiểm tra chéo transcript và chia
  theo speaker. Áp dụng: bổ sung ba clip test preview khác dữ liệu đọc trước đây.
  Ba clip chỉ thuộc miền Bắc và có hai speaker, không đại diện 63 giọng hay môi
  trường mua sắm. Chỉ dùng chẩn đoán, không fine-tune trên tập test.
- [Kumar et al., 2026 — Window Size Matters, v2](https://arxiv.org/html/2610.14607v2):
  benchmark frame-level trên noisy English; chính tác giả nêu padding, ghép chunk,
  state không reset và speech prior là giới hạn. Không suy từ AUC của protocol đó
  rằng TEN chắc chắn tốt hơn ở kiosk Việt. Giữ Silero ở native 512 mẫu/32 ms tại
  16 kHz, chạy frame processor thực và reset theo file. Chưa có boundary labels
  nên không báo F1 hay độ chính xác endpointing.
- [Ma et al., Findings EMNLP 2020 — Prefix-to-Prefix TTS](https://aclanthology.org/2020.findings-emnlp.346/):
  tách input latency và synthesis latency; ngữ cảnh trước/sau giúp nối âm tự nhiên.
  Bằng chứng trong paper là English/Chinese với model riêng, không phải Azure Việt.
  Chưa tách phát từng token vì câu retail còn phải duyệt dữ kiện; hiện đo thời gian
  tới byte audio đầu và kiểm tra huỷ phát. Chưa tuyên bố incremental TTS theo paper.
- [Dang et al., 2022 — Non-Standard Vietnamese Word Detection and Normalization for TTS](https://arxiv.org/html/2209.02971v1):
  tách nhận diện loại token và cách đọc; số, ngày, URL có thể cần ngữ cảnh khác
  nhau. Áp dụng một phần: chuẩn hóa định dạng tiền/gam/kg/ml/lít rõ bằng rule;
  không huấn luyện tagger của paper, không suy rộng điểm trên báo chí thành
  chất lượng kiosk. [Azure SSML sub](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-pronunciation#sub-element)
  cho phép giữ chữ viết và chỉ định alias đọc, dùng chung cho cả hai nhánh Azure.
  Ví dụ `300g`, `45.000đ`, `1,5 lít`; định dạng mơ hồ được giữ nguyên.

[Groq STT docs](https://console.groq.com/docs/speech-to-text) xác nhận verbose segment
metadata, temperature 0 và prompt chỉ hướng dẫn context/style. Metadata là heuristic,
không phải xác suất đúng đã hiệu chuẩn. Guard mới chặn metadata thiếu/lỗi; chưa giải
quyết mọi trường hợp đoạn tốt bị trộn câu hallucination có điểm trông vẫn cao.
[vad-web API](https://docs.vad.ricky0123.com/user-guide/api/) được đối chiếu thêm với
source package **0.0.31** đang cài; `onSpeechRealStart` và misfire khác nhau.

[LiveKit turn detector hiện tại](https://docs.livekit.io/agents/logic/turns/turn-detector/)
có cả audio model và text model. Danh sách ngôn ngữ công bố của cả hai không có
tiếng Việt tại thời điểm kiểm tra. Không tự mặc định dùng ngưỡng tiếng Anh cho Việt.
Nếu xây prototype, phải benchmark completion/continuation, backchannel, overlap và
true/false interruption riêng. Việc tích hợp LiveKit vẫn chưa thực hiện.

## Dữ liệu và replay VAD

Artifact: [Silero replay](artifacts/voice_revision_vad_controls_20261103.json).

- 12 clip FLEURS vi_vn test + 7 clip ViVoice34 đã có: 19/19 có real speech start.
- 3 clip [ViMD test preview](https://huggingface.co/datasets/nguyendv02/ViMD_Dataset)
  mới, dài 3,65–5,49 s: 3/3 có real speech start, mỗi file một segment kết thúc.
- 5 clip môi trường [ESC-50](https://github.com/karolpiczak/ESC-50), lấy filename đầu
  fold 5 của dog/door knock/keyboard/vacuum/clock alarm: 0/5 real speech start.
  Tiếng chó tạo onset rồi misfire; bốn loại còn lại không tạo onset.
- Digital silence, 440 Hz tone, seeded noise và fragment người 100 ms không tạo
  real speech start. Fragment cố ý ngắn dưới minimum, không dùng tính recall.

Nguồn ViMD có giấy phép CC-BY-NC-ND-4.0; ESC-50 CC-BY-NC-3.0 (một số clip ESC-10
có CC-BY). Audio gốc chỉ lưu `.tmp/voice_diagnostics_20261103`, không đưa vào runtime
hay phân phối lại. Manifest lưu filename, split, nguồn, license và SHA-256. Category
ESC là nhãn môi trường, chưa có người độc lập nghe xác nhận không lẫn background
speech. Đây là diagnostic nhỏ đã lộ, không phải false-positive rate toàn bộ dataset.

Model thật được chạy bằng ONNX/WASM với cấu hình production. Artifact ghi model hash,
config hash, manifest hash và sự kiện từng file. Không có nhãn boundary người chấm;
không thể suy ra false-cut/late-cut hay barge-in stop latency trên thiết bị từ các số
này. Các đoạn dài ViVoice/FLEURS có segment vượt 12 s; frontend vẫn từ chối theo cap,
không tự upload đoạn bị cắt và gọi đó là lời khách hoàn chỉnh.

Replay backend: [artifact 84 lượt](artifacts/voice_acoustic_replay_20261103.json).
Chạy decoder PyAV và model ONNX thật qua `GroqService`; ASR được fake để đo riêng
gate và số lần gọi, không dùng transcript fake làm điểm STT.

- 46 lượt speech hợp lệ được giữ lại, 34 negative controls bị chặn trước ASR;
  `false_rejects=[]`, `false_accepts=[]` trong phần input hợp lệ của tập này.
- Bốn bản ViVoice gốc Quảng Ninh, Quảng Trị, Gia Lai, Tiền Giang dài hơn 60 s trả
  input error. Chúng được ghi riêng trong `errors`, không tính là đã giữ speech
  hoặc âm không có lời. Frontend vẫn có cap riêng 12 s.
- Biến thể chọn trước theo thứ tự manifest: bốn file FLEURS đầu và năm ESC controls;
  thử MP3/WebM/MP4, gain 0,02/0,05, thêm 8 kHz và stereo cho speech. Các biến thể
  có chung audio nguồn nên không phải 84 ví dụ độc lập hoặc một benchmark mù.
- Trọng số và recurrent state không được điều chỉnh theo câu/nhãn test. Model
  cache chỉ giữ session; state/context tạo mới cho từng upload và từng pass gain.

## Kiểm thử phần mềm và API

- Frontend: **115 test qua**, gồm 13 test mới so với mốc 102 trước đợt này.
  [Artifact cuối](artifacts/voice_acoustic_frontend_20261103.json), 21 suites.
- Backend voice/Groq/Azure/TTS/lifecycle scoped cuối: **148 test qua**.
  [Artifact](artifacts/voice_acoustic_tts_scoped_20261103.xml).
- Full backend: **2.057 passed, 1 skipped, 1 warning** trong 315,87 s.
  [Log](artifacts/voice_acoustic_full_backend_20261103.txt),
  [JUnit](artifacts/voice_acoustic_full_backend_20261103.xml).
  Skip do Windows không cấp quyền tạo symlink; warning Starlette/httpx deprecation.
- Production build qua; còn warning model-viewer chunk >500 KB có từ trước.
  [Build log cuối](artifacts/voice_acoustic_build_20261103.txt).
- Ruff backend/tests/các script mới, compileall và diff-check qua.
- Kiểm tra giao demo sau restart: frontend `http://127.0.0.1:5173/` trả 200,
  backend `/health` trả 200, runtime API trỏ `http://127.0.0.1:8000`; một silence
  WAV mới trả `speech_detected=false`, transcript rỗng và không gọi Whisper.
  [Artifact smoke + source-hash](artifacts/voice_acoustic_delivery_20261103.json).
- [Hai regression frontend trước sửa](artifacts/voice_revision_app_red_20261103.json)
  ghi trạng thái bị ghi đè khi autoplay reject muộn và đổi ngôn ngữ khi STT đang chạy.
  [Regression STT rỗng trước sửa](artifacts/voice_revision_stt_red_20261103.xml)
  ghi hai lỗi retry. Artifact này là mốc giữa quá trình sửa, không phải baseline toàn
  bộ các thay đổi trong đợt này.

TTS thật sau chuẩn hóa: [ticket/stream/decode](artifacts/voice_acoustic_tts_live_20261103.json).
Vi/en/zh đều trả MP3 giải mã được; reuse vé trả 404; text quá dài trả 422; client huỷ
sau chunk đầu được ghi nhận. First audio gồm tạo ticket: vi **1.876 ms**, en 178 ms,
zh 240 ms; thời lượng audio lần lượt 10,008 / 5,856 / 4,536 s. Đây là một lần mỗi
locale, có test backend khác cùng máy; không dùng để tuyên bố giảm latency so với
lượt trước. Client đóng stream không tự chứng minh upstream giải phóng; assertion
cleanup upstream nằm trong lifecycle unit tests. Chưa có người nghe chấm phát âm
số/tiền, tên hàng hay độ tự nhiên. ElevenLabs chỉ có regression fake, không có key
để đo provider thật trong phiên này.

STT API sau restart backend: [kết quả và transcript](artifacts/voice_acoustic_stt_live_20261103.json).

| Clip ViMD | WER so với transcript dataset | Latency API |
| --- | ---: | ---: |
| `11_0309.wav` | 33,33% | 3.165 ms |
| `12_0284.wav` | 0% | 900 ms |
| `12_0286.wav` | 15% | 1.559 ms |

Ba clip đều được giữ; lỗi nhận dạng vẫn còn: clip đầu biến “mô hình cây rong
riềng” thành “mô hình dòng diệt” dù metadata confidence tốt. Không thêm bảng sửa
câu test. Năm ESC controls đều `speech_detected=false`, transcript rỗng, API
64–72 ms. Dog và alarm từng gây hallucination nay bị chặn ở acoustic gate.
Silence/tone/white noise cũng trả rỗng; empty/malformed 422, wrong MIME 415,
oversized 413. Số đo này không phải p95 hoặc accuracy của khách cửa hàng.

## Các điểm còn chưa đạt

1. Chưa nghiệm thu micro/loa kiosk: AEC, tự thu tiếng Sky, background speech, quyền
   mic, autoplay, ngắt lời khi hai bên chồng tiếng và đổi thiết bị trong browser thật.
2. Endpointing 900 ms chỉ dựa vào activity. Khách ngập ngừng lâu hoặc nói liên tục
   trên 12 s còn bất tiện. Cần tập kiosk Việt có timestamp/boundary labels trước
   khi thay cap/threshold hoặc thêm semantic detector.
3. STT đang chạy sau đoạn hoàn chỉnh, chưa hiện partial transcript. Server Silero
   kiểm tra có lời nói, không xác nhận người nói hay đúng từng từ. Speech trộn
   nhạc/tiếng ồn và tiếng Sky tự thu lại có thể qua gate. Metadata tốt vẫn nhận sai.
4. Chưa có subjective TTS MOS/intelligibility hoặc benchmark tên SKU/tiền/đơn vị
   do người Việt chấm; audio decode chỉ xác nhận transport/codec.
5. Tư vấn như nhân viên vẫn cần đánh giá nội dung độc lập; voice tests không nâng
   được điểm chất lượng tư vấn khi provider quota hoặc dữ kiện hàng hoá còn thiếu.

Lệnh chạy lại có trong `evaluation/collect_voice_diagnostic_samples.py`,
`audit_local_vad.mjs`, `audit_server_speech_activity.py` và `audit_voice_pipeline.py`.
Không chạy lại liên tục chat LLM
đã hết daily quota để lấy một lượt đẹp; STT/TTS được đo riêng và giữ lỗi thực tế.

## Hướng tiếp theo và điều kiện nghiệm thu

1. Thu tập hội thoại kiosk Việt với đồng ý của người tham gia, chia theo speaker
   và thiết bị; giữ tập chưa dùng phát triển. Gán nhãn từ quan trọng (tên hàng,
   số tiền, số lượng), thời điểm hết lượt, ngập ngừng và chồng tiếng.
2. Đo WER/CER cùng sai slot tiền/số lượng, early/late endpoint, false interruption,
   latency từ hết lời đến tiếng trả lời đầu; thử cả speech trộn noise và echo loa.
   Chỉ đổi VAD/endpointing/streaming ASR khi ablation trên cùng input chứng minh lợi ích.
3. Người Việt nghe/chấm tên hàng, tiền/đơn vị, độ dễ hiểu và tự nhiên của TTS;
   giữ riêng kết quả transport/codec và cảm nhận. SSML hợp lệ không tự chứng minh
   phát âm đã đúng hoặc đạt MOS.
4. Nếu prototype LiveKit, kiểm tra reconnect, cancel, barge-in và threshold tiếng
   Việt riêng; không mặc định turn detector đa ngôn ngữ hiện công bố hỗ trợ Việt.
