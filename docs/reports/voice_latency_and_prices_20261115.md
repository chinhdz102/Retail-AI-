# Tốc độ thoại và lỗi hỏi giá — 15/11/2026

## Kết quả thực tế

Đã tối ưu khoảng chờ STT và khởi động TTS trên demo localhost 5173/8000.
STT đang dùng Azure conversation `vi-VN`; chưa đổi model nhận diện. Đường
thoại này dùng HTTP, chưa đi qua LiveKit. TTS đã stream MP3; LLM vẫn trả
một kết quả hoàn chỉnh sau khi kiểm tra nội dung.

Trong Chrome thật, cùng câu tổng hợp “Cho mình thịt bò để nhúng lẩu.”,
Silero/WASM/AudioWorklet và STT/TTS cloud thật:

| Chế độ | Sau VAD chốt đến chữ sẵn sàng | Từ frame lời nói cuối đến browser phát TTS |
|---|---:|---:|
| Chờ VAD rồi gửi WAV, lần 1 | 1334 ms | 3170 ms |
| Nhận diện trong khoảng nghỉ, lần 1 | 11 ms | 1774 ms |
| Chờ VAD rồi gửi WAV, lần 2 | 879 ms | 2690 ms |
| Nhận diện trong khoảng nghỉ, lần 2 | 72 ms | 1790 ms |

Cả bốn đều nhận đúng câu mẫu. Cột TTS dùng cùng một câu kiểm tra giọng nói,
không có LLM. Các số này là bốn lượt thử, không phải p50/p95 hoặc đo phòng
thật. Dùng đầu vào WAV tổng hợp qua Web Audio, chưa đo mic/loa vật lý.
Mạng/provider có dao động; không quy mọi chênh lệch cho code.

## Thay đổi đã thực hiện

- VAD vẫn đợi 1440 ms và giữ 640 ms trước lời nói. Không rút thời gian chờ
  để đổi lấy việc cắt câu. Sau 512 ms im lặng rõ, gửi WAV tạm sang cùng
  pipeline STT; không hiển thị/gửi trợ lý trước khi chốt lượt.
- Hủy kết quả tạm khi có bằng chứng lời nói trở lại, kể cả frame không chắc
  có xác suất từ 0,1 trở lên. Chốt lượt chỉ dùng kết quả có ID khớp và cùng
  phiên. Lỗi/rỗng/bị từ chối thì nhận diện lại WAV đầy đủ. Giới hạn hai lần
  thử tạm mỗi lượt; nhiều khoảng nghỉ hơn sẽ quay về đường WAV đầy đủ.
- Dừng mic/đổi phiên/đóng trang hủy công việc tạm. Backend theo dõi client
  disconnect và hủy request ASR đang chờ; không giải phóng giả chỗ của worker
  native đang chạy.
- Nạp sẵn Silero/YAMNet lúc backend khởi động. Tách acoustic/preparation/
  provider/debug trace trong `quality`, thay vì suy đoán tất cả chậm do model.
- Gọi preconnect TTS khi bật mic/bắt đầu nói; dùng lại HTTP client và gộp
  các lần prepare đồng thời, TTL 45 giây. Đóng client sở hữu khi tắt backend.
  Audio vẫn phát từ stream ngay khi có dữ liệu; không chờ tải hết MP3.
- Ghi thời gian riêng cho Plan/Composition/AdviceReview của tư vấn.

Mức overlap tối đa theo cấu hình là 928 ms (1440 − 512). Trong browser thực,
ngưỡng im lặng chặt hơn khiến overlap khoảng 800 ms ở câu mẫu. Đây là batch
ASR chạy sớm, chưa phải streaming STT hoặc stream token/câu từ LLM.

## Giữ phần sửa ý và lời nói trong tiếng quạt

Hai ca Chrome/cloud thật bổ sung:

| Audio | Kết quả | Sau endpoint đến chữ |
|---|---|---:|
| Nam Minh, nghỉ rồi sửa ý | “Em muốn mua sữa ít đường. Không đường.” | 634 ms |
| Nam Minh, nền quạt SNR 5 dB | “Bỏ bánh bao ra, mình muốn mua thịt tươi.” | 650 ms |

Ca sửa ý phát sinh hai upload: upload đầu bị abort khi nói tiếp, upload cuối
giữ cả hai vế. Chỉ một kết quả cuối được dùng. Unit test với FrameProcessor
thật cũng so sánh toàn bộ PCM qua khoảng nghỉ 1024 ms và phần sửa ý.
Hai ca bổ sung này không đại diện cho tất cả giọng vùng miền/mức ồn.

## STT/TTS ở API sau warmup

| Phép đo thật | Kết quả |
|---|---|
| STT đầu sau restart, WAV 4,448 s | API 2341 ms: acoustic 54,6; chuẩn bị 0,8; Azure 2270,5; trace 10,5 ms |
| STT tiếp theo, WAV 3,680 s | API 829 ms: acoustic 42,2; chuẩn bị 0,7; Azure 774,6; trace 5,5 ms |
| TTS 40 ký tự | Ticket 2 ms; byte đầu 243 ms; hoàn tất 366 ms |
| TTS 123 ký tự | Ticket 4 ms; byte đầu 168 ms; hoàn tất 341 ms |

Prepare đầu tiên mất 1862 ms, được gọi trước/trong lúc nói và không chặn mở
mic. STT đầu vẫn còn chi phí kết nối/provider; trong lượt này phần Azure
chiếm gần hết thời gian. Baseline trước sửa ghi 4914/833 ms STT và 892/279 ms
TTS byte đầu, nhưng chưa tách acoustic/provider nên không suy ra chính xác
mỗi kỹ thuật tiết kiệm bao nhiêu. Byte đầu API không phải thời điểm loa phát.

## Lỗi hỏi giá đã sửa

“Ờ ờ các loại thịt giá như thế nào?” đã được nhận fields giá nhưng vẫn đi
vào nhánh tư vấn; model có thể trả kiến thức chọn thịt chung. Nay câu chỉ
hỏi giá đi thẳng tra catalog, lấy giá và đơn vị thật, không gọi model.
Tư vấn/budget/so sánh vẫn qua LLM. Tra danh mục dùng taxonomy đầy đủ thay
vì top 5 lexical khiến bánh/snack chen vào nhóm thịt.

Sửa thêm việc bỏ dấu khiến danh từ “sữa” bị coi là động từ “sửa”, chặn hỏi
giá sữa/sữa tắm như yêu cầu thay đổi dữ liệu. Nhãn hãng/tên mơ hồ vẫn hỏi
đúng biến thể; không chốt tùy tiện một mức giá. Xóa tham chiếu sản phẩm đơn
cũ khi vừa trả danh sách nhiều loại, giữ tham chiếu đúng khi full tên duy nhất.

HTTP thật: 8/8 câu hỏi giá thịt/thịt heo/dầu gội/sữa tắm/nước giặt/rau/sữa
trả 200, 46–102 ms trong lần kiểm tra cuối. Ví dụ câu khách đưa:

> Thịt Thăn bò: 399.000đ/kg; MEATDELI Nạc vai heo: 168.900đ/kg;
> bò xay khay 200g: 68.700đ/khay; phi lê ức gà: 112.900đ/kg;
> PACOW nạm bụng bò 250g: 106.900đ/khay.

Banner lỗi chat trước đây nói mất kết nối quá rộng. Nay phân biệt tạm hết
lượt/không hoàn tất câu/timeout; lỗi giỏ snapshot không làm hỏng chat. Log
đã có lỗi JSON/provider/rate limit; không suy rằng tất cả lỗi là hết key.
Catalog cũ giữ nguyên SHA256
`a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`.

## Nút thắt còn lại

Một câu tư vấn thật sau bộ giá: “Thịt heo để ăn lẩu thì chọn gì?” trả 200
trong 7321 ms, chọn ba rọi MEATDELI 500 g, tổng 99.950đ. Các stage log:
Plan 4578 ms (primary lỗi JSON, chuyển model), Composition 1505 + 682 ms
(cần sửa draft), AdviceReview 488 ms. Đây là thời gian LLM trước TTS,
chưa đủ nhanh để gọi cả hội thoại là tức thời. Lần trước cùng loại lượt
còn mất gần 40 giây; dao động provider/retry vẫn cần theo dõi.

Chưa phát draft chưa kiểm tra cho khách để tạo cảm giác nhanh. Bước kiến
trúc tiếp theo là giảm vòng sửa JSON, theo dõi retry từng stage rồi cân
nhắc stream từng câu đã qua kiểm tra. Nếu chuyển sang ASR continuous cần
giữ partial/final, cancellation, reconnect và phần sửa ý như các kiểm tra
ở bản này. Chưa đánh giá đủ để khẳng định tư vấn mọi ngành hàng đã đạt
mức nhân viên, hoặc mọi lỗi nhận dấu đã hết.

## Nguồn kỹ thuật đã đối chiếu

- Microsoft: [Lower speech synthesis latency](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-lower-speech-synthesis-latency):
  preconnect khi khách bắt đầu nói, tái sử dụng kết nối/synthesizer, phân
  biệt first-byte với hoàn tất và độ trễ client. Bản REST này dùng reusable
  HTTP client + voices-list request để làm ấm, không gọi đó là SDK WebSocket.
- LiveKit: [Turn-taking tuning](https://docs.livekit.io/agents/logic/turns/tuning/),
  [Turn handling options](https://docs.livekit.io/reference/agents/turn-handling-options/):
  tách endpointing, interruption và xử lý suy đoán; chỉ dùng kết quả sau
  khi turn hợp lệ, bỏ công việc cũ khi khách tiếp tục. Áp dụng nguyên lý
  overlap cho ASR hiện có; chưa bật LiveKit/preemptive LLM trong demo.
- Ellinas et al., Interspeech 2020:
  [High Quality Streaming Speech Synthesis with Low, Sentence-Length-Independent Latency](https://www.isca-archive.org/interspeech_2020/ellinas20_interspeech.pdf).
  Đây là hướng nghiên cứu streaming TTS; không dùng số đo của paper làm
  kết quả của dự án, không thay giọng/model bằng mô hình của paper.

## Kiểm tra và tái chạy

- Backend toàn bộ: **834 passed**, dùng `CATALOG_REPOSITORY=demo` cho các
  legacy API test vốn thiết kế với demo; không sửa `.env` runtime snapshot.
- Frontend toàn bộ: **66 passed**; build TypeScript/Vite thành công.
- Có regression cho hủy preview, stale ID/phiên, Stop, nhiều khoảng nghỉ,
  preview lỗi/rỗng, PCM qua khoảng nghỉ, và ngắt TTS giữ buffer câu mới.
- Chrome/cloud: 6 lượt trong artifact mới, cả 6 không có lỗi JS, đều có
  transcript chấp nhận. Bốn lượt cùng câu so sánh on/off; một sửa ý; một quạt.
- Ruff trên các module acoustic/STT/TTS/price được sửa: qua. Các file lớn
  consultation/main có nợ style từ trước; không tự format toàn bộ chúng.
- Build vẫn cảnh báo chunk model-viewer lớn; jsdom báo API canvas/media
  không triển khai. Phép đo Chrome không dùng jsdom.

Bằng chứng:

- `artifacts/voice_latency_before_overlap_20261115.json`
- `artifacts/voice_latency_after_warmup_20261115.json`
- `artifacts/voice_latency_browser_overlap_20261115.json`
- `artifacts/voice_latency_app_20261115.json`
- `artifacts/price_intent_live_20261115.json`
- `artifacts/price_intent_prices_final_20261115.json`

Chạy lại khi backend/frontend đã bật và có cấu hình Azure:

```powershell
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m evaluation.audit_voice_latency --prepare --output docs/reports/artifacts/voice_latency_after_warmup_20261115.json
node apps/kiosk-airi/scripts/audit-latency-browser.mjs
.venv\Scripts\python.exe -m evaluation.audit_price_intent
```

Probe browser đọc WAV tổng hợp trong `artifacts/stt-input-captures`; không
tự thu mic vật lý và không dùng dữ liệu khách. Không import probe vào app.

Một lượt toàn bộ giao diện thật (Chrome, Azure STT, Groq tư vấn và Azure
streaming TTS) cũng hoàn tất: câu nhận được “Cho mình thịt bò để nhúng lẩu.”,
Sky trả một lựa chọn có giá/giải thích, và audio phát được. Cart snapshot trả
503 nhưng UI vẫn tiếp tục hội thoại; đó là nhánh giỏ demo đã được bắt lỗi.
Phép đo này chạy một lượt nên dùng để kiểm tra luồng, không dùng làm p50/p95.
