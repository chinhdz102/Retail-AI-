# Kiểm tra lỗi code STT và dấu tiếng Việt — 10/11/2026

**Đã tái hiện và sửa một lỗi code có thể làm mất phần đầu câu:** app tự mở lại
mic khi danh sách thiết bị cập nhật muộn. Chưa có bản thu của lượt người dùng
nói “lẩu” bị chép thành “lấu”, nên chưa xác định nguyên nhân riêng của lượt đó.
Không dùng kết quả file chuẩn để kết luận mic thật hoặc code thu âm đã đúng hết.

Demo tiếp tục dùng bản cũ tại worktree `vietnamese-whisper-stt`. STT đang chạy
Groq `whisper-large-v3`, khóa `vi`, temperature 0, không prompt, không sửa dấu
bằng từ điển hay LLM. Không đổi model hoặc bộ dữ liệu sản phẩm trong lượt này.

## 1. Lỗi code đã chứng minh

`handleVoiceState(listening)` gọi `refreshMicrophones()` bất đồng bộ. Nếu khách
đã bắt đầu nói trước khi `enumerateDevices()` trả về, code cũ vẫn gọi
`changeMicrophone()` khi chọn được thiết bị khác. Hàm này dừng bộ thu rồi mở
lại; `pause()` của VAD bỏ buffer đang giữ. Phần đầu câu không còn trong WAV
gửi STT. Một kết quả quét thiết bị cũ cũng có thể ghi đè lựa chọn mic mới.

Bài kiểm tra chủ động trì hoãn kết quả tìm mic đến sau trạng thái `recording`.
Trước sửa, bộ thu mở **2 lần**, trong khi cần giữ nguyên **1 lần**; bài kiểm tra
thất bại đúng điều kiện này. Đây là lỗi luồng code, không phải đánh giá model.

Đã sửa trong `apps/kiosk-airi/src/App.vue`:

- Tự phát hiện thiết bị chỉ cập nhật đầu vào của lượt thu tiếp theo, giữ nguyên
  bộ thu và âm thanh đang có. Người dùng chủ động chọn mic vẫn được đổi ngay.
- Gắn số thứ tự cho mỗi lần quét. Bỏ kết quả cũ, kết quả sau khi đóng trang,
  hoặc kết quả bắt đầu trước thao tác chọn mic mới.
- Thêm hai ca hồi quy: đang nói thì danh sách mic mới trả về; đang quét mic
  thì người dùng chọn thiết bị khác.

[Bằng chứng trước/sau](artifacts/stt_microphone_restart_repro_20261110.json).
Chưa có bằng chứng lỗi mở lại mic gây đúng cặp dấu “lẩu/lấu”; không gộp hai
kết luận này thành một.

## 2. Kiểm tra đường âm thanh trong Chrome

Chạy 6 câu tiếng Việt qua thiết bị mic giả của Chrome, mỗi câu hai cấu hình:
đúng thiết lập đang dùng của app và tắt cả AEC/NS/AGC để đối chiếu. Có giọng
nam/nữ, “ăn lẩu”, “nước lau sàn”, “sữa ít đường”, “sửa đơn”.

Đây là **12 lượt getUserMedia → xử lý âm của Chrome → AudioWorklet → Silero →
PCM WAV → HTTP STT thật**. Không mở mic vật lý. File có khoảng im lặng đầu để
đo luồng sau khi sẵn sàng; không dùng phép thử này để kết luận về lúc mở mic
lạnh, âm phòng hay giọng người dùng.

- Cấu hình sản phẩm trả track 48 kHz, cấu hình tắt xử lý trả 44,1 kHz;
  AudioContext thực tế đều là 16 kHz. WAV xuất cũng là mono PCM16 16 kHz.
- Cả 12 lượt gửi đúng một WAV sau khi dừng track, không lỗi API hay bộ thu.
- So WAV với chính các frame đầu vào worklet: phần được giữ chứa toàn bộ năng
  lượng tín hiệu của các mẫu này; cosine similarity thấp nhất 0,9999999726.
  Không thấy mất phần có âm do bước ghép frame/cắt VAD trong bộ thử này.
- Có 19 mẫu vượt biên độ 1 một chút ở đầu vào đã qua xử lý; bộ mã hóa WAV
  giới hạn về miền PCM. Tỷ lệ cao nhất trong một WAV là 0,0115%. Chưa có bằng
  chứng phần giới hạn biên độ rất nhỏ này gây lỗi từ/dấu.
- Chỉ **8/12 câu đúng hoàn toàn**. Hai giọng “nước lau sàn” thành “nước lau
  sàng” hoặc “nước lau sang” ở cả hai cấu hình. Các câu “ăn lẩu” đều đúng.
  Trong bộ thử này, tắt toàn bộ lọc âm không giải quyết lỗi chữ.

Giới hạn: mic giả và TTS không thay thế phép đo mic laptop trong phòng thật.
Ca thiết bị cập nhật giữa câu được kiểm tra riêng ở mục 1; trang đo worklet
không chạy bước tự chọn thiết bị của App.

[Kết quả Chrome](artifacts/stt_browser_input_20261110.json) ·
[Đối chiếu frame/WAV và điểm chữ](artifacts/vietnamese_tones_summary_20261110.json).

## 3. So sánh nhận dạng trên cùng file

Manifest gồm 45 file: 24 câu tổng hợp sạch (12 câu × hai giọng), 6 bản thêm
nhiễu nhân tạo 10 dB, 12 bản ghi người thật từ FLEURS tiếng Việt và 3 file
không có lời (ho, tiếng bò, im lặng). Hash âm thanh được đối chiếu giữa các
lần chạy. Nhãn tham chiếu chỉ dùng chấm kết quả, không gửi vào STT.

| Cấu hình | Câu tổng hợp đúng /24 | Lỗi âm tiết trên 12 file FLEURS | FLEURS đúng toàn câu /12 | File không lời có chữ /3 |
| --- | ---: | ---: | ---: | ---: |
| Whisper large-v3 gọi trực tiếp | 22 | 7,41% (26/351) | 4 | 3 |
| large-v3 + từ vựng cố định | 24 | 7,12% (25/351) | 4 | 3 |
| Whisper large-v3-turbo trực tiếp | 21 | 9,40% (33/351) | 2 | 3 |
| Azure conversation vi-VN trực tiếp | 22 | 5,41% (19/351) | 4 | 1 |
| PhoASR small, cách chạy chính thức | 20 | 6,84% (24/351) | 4 | 3 |
| API demo, có kiểm tra âm thanh trước STT | 22 | 7,41% (26/351) | 4 | 0 |

Cả sáu cấu hình đều đúng 6/6 bản nhiễu nhân tạo; tập rất nhỏ này chưa đủ đo
lọc tiếng quạt/điều hòa thực tế. API demo trả HTTP 200 cho 45/45 file, nhưng
**HTTP thành công không có nghĩa chép đúng**. Bộ kiểm tra âm thanh ngăn cả ba
file không lời đi thành câu tư vấn; cần giữ bước này khi thử decoder khác.

Một request so sánh có lỗi kết nối/quota đã được chạy lại riêng và ghép đúng
ID/hash; bảng dùng đủ 12/12 file FLEURS. Thời gian lần chạy lại không có trong
artifact, nên thống kê latency của cấu hình prompt trên FLEURS có 11 số đo.
Không bỏ mẫu lỗi khỏi mẫu số để làm đẹp kết quả.

PhoASR dùng `Qualcomm-AI-Research/PhoASR-whisper-small`, revision
`da2d9ae0ac0a6a6faba20add61c2e980ccd85037`, chạy Transformers trên CPU.
Median FLEURS khoảng 15,48 giây, so với 0,94 giây cho large-v3 gọi trực tiếp;
hai môi trường khác nhau nên đây là thời gian thực nghiệm trên máy này.
Thử chuyển CT2 ban đầu cho câu cụt, không tương thích cách giải mã cần
timestamp của model; đã loại kết quả CT2 đó khỏi bảng, chạy lại toàn bộ bằng
cách inference chính thức.

Không tự chuyển demo sang model khác từ bảng này: chỉ 12 file người thật,
chưa phải hội thoại cửa hàng hay các giọng vùng miền của khách. Giọng tổng
hợp do Azure tạo nên có thể thiên vị Azure STT. Từ vựng prompt trùng chủ đề
của tập phát triển tổng hợp; 24/24 không phải kết quả kiểm tra độc lập.

## 4. Kiểm tra dấu và nghiên cứu

Bổ sung phép chấm giữ nguyên dấu: chuẩn hóa Unicode NFC, căn chỉnh âm tiết,
tách lỗi chỉ đổi thanh khỏi lỗi đổi hình nguyên âm và lỗi thêm/bớt từ. Ví dụ
“lẩu → lấu” được tính lỗi hỏi → sắc; “sữa → sửa” cũng là lỗi thanh. Không bỏ
dấu trước khi tính độ chính xác và không dùng phép chấm này sửa lời khách.

FLEURS có ví dụ “cổ → cô”. Trường hợp nhãn “hiên” và kết quả “hiện” trong
“thực hiện” có khả năng là lỗi chính tả của nhãn. Giữ nguyên nhãn để tái lập
bảng điểm và ghi riêng vấn đề này; không coi cả hai là hai lỗi thanh đã được
chứng minh. Chưa tái hiện được chính “lẩu → lấu” trong các file hiện có.

Các nguồn chính được đối chiếu:

- [Tone in Perspective, SIGTYP 2025](https://aclanthology.org/2025.sigtyp-1.11/):
  làm phẳng cao độ gây suy giảm mạnh hơn ở ngôn ngữ có thanh điệu. Điều này
  ủng hộ kiểm tra âm thanh trước/sau xử lý; không chứng minh NS/AGC là nguyên
  nhân trên laptop này.
- [PhoWhisper, 2024](https://arxiv.org/html/2406.02555v1): dữ liệu tiếng Việt
  đa giọng và điều kiện âm thanh là hướng thích nghi cần đo, không phải chỉ
  tăng tên/kích thước model.
- [Vietnamese ASR: A Revisit, EACL 2026](https://aclanthology.org/2026.findings-eacl.345/)
  và [model PhoASR hiện tại](https://huggingface.co/Qualcomm-AI-Research/PhoASR-whisper-small):
  thử model chuyên tiếng Việt, ghim revision và làm đúng inference. Model
  card cũng nêu giới hạn giọng miền Trung; không suy ra tốt cho mọi khách.
- [Contextual ASR / LLM re-predictor, Interspeech 2024](https://www.isca-archive.org/interspeech_2024/manhtienanh24_interspeech.pdf)
  và [bias word selection, Interspeech 2025](https://www.isca-archive.org/interspeech_2025/hou25_interspeech.html):
  ngữ cảnh chọn lọc đáng thử, nhưng phải đo cả kéo nhầm lời nói về tên hàng.
  Không dùng LLM tự viết lại transcript thành câu nghe có vẻ hợp lý.
- [Groq STT](https://console.groq.com/docs/speech-to-text) và
  [Azure STT REST](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-speech-to-text-short):
  kiểm tra ngôn ngữ, định dạng PCM và giới hạn dịch vụ; endpoint REST của
  Azure trong phép so sánh chỉ trả kết quả cuối, chưa phải luồng streaming.

Hướng xử lý tiếp cho lượt nhận sai ngoài thực tế là đối chiếu đúng WAV đã gửi
ở lượt lỗi, thời điểm mic mở/đóng và phản hồi STT nguyên gốc. Nếu câu trong WAV
bị cắt thì sửa capture/endpoint trước. Nếu WAV đầy đủ nhưng chữ sai thì mới
thử ngữ cảnh ngắn hoặc decoder khác trên một tập giữ riêng. Các thao tác thay
đổi giỏ cần được đánh giá thêm về giữ đúng từ phủ định, số lượng và loại hàng.

## 5. Xác minh và phạm vi

- 45/45 bài frontend qua ở lần xác minh cuối, gồm hai hồi quy về mic và ba
  hồi quy giữ nguyên dấu; build/typecheck qua.
- 54/54 bài kiểm tra STT/backend và phép chấm dấu qua; Ruff phần mới sạch.
- 12/12 lượt Chrome hoàn tất gửi âm thanh đúng luồng; độ đúng chữ là 8/12.
- 45/45 lượt API demo hoàn tất; kết quả chữ và các lần gọi decoder được giữ
  theo từng file trong artifact, không thay bằng đánh giá cảm tính.
- Chưa kiểm chứng lại giọng, mic và tiếng nền phòng của người dùng. Chưa thể
  tuyên bố toàn bộ lỗi “nhận diện lung tung” đã hết.

[Kết quả frontend cuối](artifacts/stt_frontend_final_tests_20261110.json) ·
[Kết quả STT/backend](artifacts/stt_scoped_tests_20261110.xml) ·
[So sánh đầy đủ](artifacts/vietnamese_tones_summary_20261110.json) ·
[Báo cáo catalog cũ](catalog_old_diagnosis_20261110.md).

## 6. Đối chiếu nguyên gốc STT → API → giao diện

Người dùng không nhớ chữ “lấu” nằm ở transcript hay câu Sky trả lời. Vì vậy
không thể gán nguyên nhân chỉ từ câu trả lời hoặc tự sửa tất cả “lấu” thành “lẩu”.

Đã thêm trace phát triển theo UUID của từng request, chỉ bật khi
`STT_DEBUG_CAPTURE=true` và `APP_ENV=development`. Trace lưu file nhận từ app,
WAV thực gửi cho STT, hash từng file, phản hồi nguyên gốc của dịch vụ và kết quả
API. Không có endpoint công khai để tải các file này, không ghi audio/text vào
application log. Chỉ giữ tối đa 12 lượt; các thư mục quá 30 phút được dọn **khi
có lượt capture tiếp theo**, không phải bộ hẹn giờ tự xóa sau đúng 30 phút.

Chạy lại **6 file qua HTTP thật**, gồm hai giọng ở câu “ăn lẩu” sạch/nhiễu và
hai giọng ở câu “nước lau sàn”:

| Đối chiếu | Kết quả |
| --- | --- |
| Hash file gửi khớp file backend nhận | 6/6 |
| Dấu trong phản hồi dịch vụ được giữ nguyên qua API | 6/6 |
| “Ăn lẩu” đúng | 4/4 |
| “Nước lau sàn” đúng | 0/2: dịch vụ đã trả “sàng”/“sang” |

Hai lỗi “sàn” này xuất hiện **trong phản hồi gốc của dịch vụ**, trước lúc API
trả chữ. Đây là kết luận về hai mẫu đã đo, không phải kết luận mọi lỗi đều do
model. Luồng thu mic thật vẫn có thể có lỗi khác; chưa có file của lượt
“lẩu → lấu” của người dùng.

Thêm ba bài frontend đưa nguyên văn kết quả STT “lẩu”, “lấu”, “sàng” vào App:
cả dòng lời khách lẫn text gửi `/agent/respond` phải giữ nguyên dấu đó. Đây là
bài kiểm tra với STT giả lập để kiểm tra code truyền chữ, không phải phép đo
độ chính xác nhận dạng. Toàn bộ frontend hiện **45/45 bài qua**, build/typecheck
qua; build còn cảnh báo kích thước phần hiển thị avatar.
[Kết quả 45 bài frontend](artifacts/stt_frontend_final_tests_20261110.json).

[Bằng chứng 6 lượt HTTP](artifacts/stt_trace_roundtrip_20261110.json).

**Kết luận còn mở:** đã sửa lỗi mở lại mic giữa câu và đã có cách xác định chữ
sai phát sinh trước hay sau API. Chưa chứng minh được nguyên nhân riêng của
lượt “lẩu → lấu”; không tuyên bố đã sửa hết lỗi dấu hoặc đã kiểm chứng mic
vật lý trong phòng người dùng.

Chạy lại kiểm tra không cần dịch vụ:

```powershell
cd apps/kiosk-airi
npm run test:run
npm run build
```

Các chương trình `evaluation/research_vietnamese_tones.py` và
`apps/kiosk-airi/scripts/audit-stt-input.mjs` có gọi STT thật; manifest phải có
file âm thanh đúng hash. PhoASR chạy trong môi trường nghiên cứu tách riêng,
không thêm Torch/Transformers vào môi trường của demo.
