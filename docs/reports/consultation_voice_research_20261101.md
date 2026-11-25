# Nghiên cứu bổ sung: tư vấn có căn cứ và luồng thoại

Ngày 01/11/2026. Đây là ghi chú thiết kế và nguồn đã đọc, không phải chứng nhận chất
lượng. [Kế hoạch](consultation_voice_workplan_20261101.md) có tiêu chí kiểm chứng;
[báo cáo kết quả](consultation_voice_audit_20261101.md) ghi riêng những gì thực sự chạy.

## Paper và quyết định áp dụng

Các paper ProductAgent/PROCLARE, PEPPER và survey CRS đã được đọc ở
[đợt nghiên cứu trước](customer_consultation_research_20261101.md), cùng hash tài liệu.
Chúng dẫn tới ba yêu cầu: xác định hành động hội thoại trước truy xuất, lưu sở thích/
câu hỏi còn thiếu, và đánh giá tiến triển qua nhiều lượt thay vì chỉ chấm đúng SKU.
35 câu người dùng đưa là development set đã lộ; journeys tự viết cũng không phải blind set.

Đợt này đọc thêm Gao và cộng sự, **Enabling Large Language Models to Generate Text
with Citations**, EMNLP 2023, đặc biệt phần 3.2–3.3:
<https://aclanthology.org/2023.emnlp-main.398/> và
<https://aclanthology.org/2023.emnlp-main.398.pdf>.

ALCE phân biệt độ trôi chảy, độ đúng và chất lượng dẫn chứng. Citation recall đòi hỏi
mỗi phát biểu được toàn bộ nguồn dẫn hỗ trợ; citation precision xét nguồn không liên
quan. Có nguồn trong context không tự chứng minh câu trả lời trung thành với nguồn.
Paper dùng NLI và đối chiếu con người trong miền của họ; ứng dụng này **chưa triển khai
NLI/citation recall của ALCE** và không được lấy điểm paper làm điểm bán lẻ tiếng Việt.

Áp dụng thực tế:

- Đưa `ProductKnowledgeFact` vào bước viết câu, giữ trạng thái VERIFIED/INFERRED/UNKNOWN,
  phạm vi, nguồn, thời điểm và PRODUCT_LINE/EXACT_VARIANT. Không đưa HTML mô tả tự do.
- Thêm một lượt kiểm tra bản nháp riêng: phù hợp mục đích, ràng buộc, giỏ/số lượng,
  các phát biểu thiếu nguồn, câu dang dở và việc hỏi lại. Cho sửa một lần, nếu vẫn bị
  bác thì hỏi tiếp thay vì công bố picks đã bị bác.
- Kiểm tra cả phần tên/giá an toàn khi phải hạ cấp cách diễn đạt; một fallback chứa giá
  đúng vẫn có thể chọn sai hàng. Giá có dấu cách Unicode và từng tên được máy chủ ràng buộc.
- **Đã thấy reviewer chấp thuận sai**: suy diễn vị ngọt từ tên không đường, suy diễn công
  dụng/cách dùng từ tên thịt. Lượt review bổ sung cũng tăng chi phí/độ trễ và gặp quota.
  Không dùng reviewer làm cổng phát hành duy nhất; kết quả thử nghiệm chưa đủ để bật mặc định.

## Dữ liệu thật và giới hạn phạm vi

Catalog có 3.081 sản phẩm được chấp nhận, 18 dòng cách ly; đây là snapshot, không phải
tồn kho/giá trực tiếp của cửa hàng. Registry enrichment hiện có kiểm tra ID, tên, hãng,
barcode, hash catalog và phạm vi biến thể. Không mở rộng công dụng từ vài SKU sang cả ngành.

Đối chiếu lại nguồn hãng thực tế:

| Nguồn đã tải và đọc | Dữ kiện dùng được trong đúng phạm vi | Điều không được suy ra |
| --- | --- | --- |
| [Coca-Cola Việt Nam](https://www.coca-cola.com/vn/vi/brands/coca-cola) | Bảng Zero Sugar trên trang ghi đường 0 g/100 ml, có chất tạo ngọt; là công bố của hãng | Không đường không chứng minh không ngọt, phù hợp mọi bệnh lý hoặc mọi biến thể |
| [Acecook Hảo Hảo](https://acecookvietnam.vn/product/mi-hao-hao/) | Hướng dẫn của phần mì nước: 400 ml nước sôi, khoảng 3 phút | Không chuyển sang mì xào, mọi quy cách hoặc suy ra đủ chất/đủ bữa |
| Registry `product_knowledge_enrichment_pilot.json` | Các fact đã có nguồn và phạm vi của Coke/P/S/Hảo Hảo; Pantene có marketing INFERRED | Marketing không trở thành hiệu quả được kiểm chứng hay bảo đảm an toàn |

Nền nghiệp vụ: [Codex CXS 1-1985](https://www.fao.org/4/Y2770E/y2770e02.htm) và
[GS1 Global Data Model](https://www.gs1.org/standards/gs1-global-data-model-attribute-implementation-guide/13-1).
Hệ thống vẫn thiếu phần lớn nhãn thành phần, dị ứng, hướng dẫn, độ lưu mùi, hiệu quả
và thông tin phù hợp da/thú cưng. Nghiên cứu hoặc prompt không lấp được những trường này.

## STT, VAD và TTS: hướng dẫn chính thức

| Tài liệu | Điểm đối chiếu với mã | Giới hạn kết luận |
| --- | --- | --- |
| [Groq Speech to Text](https://console.groq.com/docs/speech-to-text) | 16 kHz mono phù hợp; prompt là ngữ cảnh ngắn, không phải transcript; kiểm tra verbose segment quality; một deadline cho primary + fallback | Độ tự tin không bảo đảm nghe đúng tên hàng/giọng vùng miền |
| [vad-web API](https://docs.vad.ricky0123.com/user-guide/api/) | `onSpeechStart` có thể dẫn đến misfire; `onSpeechRealStart` xác nhận đủ các frame speech. Đã đối chiếu cả mã package 0.0.31 đang cài | Trang mới có model/default khác; chỉ replay v5 đang thực sự chạy |
| [Silero VAD README](https://github.com/snakers4/silero-vad) | VAD phân loại có tiếng nói, không xác định ý nghĩa lượt nói hay nhận dạng người nói | Không suy ra chống echo, kết thúc câu đúng ý hoặc hiểu mọi giọng Việt |
| [Azure SSML pronunciation](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-pronunciation) | Nội dung được XML escape; có giọng vi/en/zh thực sự gọi được; test tiền, đơn vị và ký tự XML | Audio giải mã được không chứng minh tiền/đơn vị đọc đúng hoặc giọng tự nhiên; chưa dùng say-as tùy tiện khi chưa xác minh hỗ trợ locale |
| [LiveKit turn handling](https://docs.livekit.io/agents/logic/turns/) | Phân biệt endpointing, interruption, backchannel và false interruption; LiveKit có cơ chế resume false interruption | Repo không có LiveKit runtime/dependency/config; chưa có kiểm thử phòng/participant hoặc tích hợp LiveKit |

Luồng thật hiện tại là browser Silero v5/ONNX → WAV hoặc MediaRecorder → Groq Whisper
hoàn tất đoạn → agent → Azure/ElevenLabs ticket stream → browser audio/fallback.
SSE trả lời không đồng nghĩa toàn bộ pipeline đã incremental streaming.

Việc đợi `onSpeechRealStart` chỉ giảm ngắt nhầm vì onset quá ngắn. Chưa có semantic turn
detector hay cơ chế nhận biết “ừ/à” là backchannel. Kiosk hiện giữ câu TTS bị ngắt trong
TTL 15 giây và phát lại bằng ticket mới khi STT sau đó trả rỗng/non-speech/lặp; đây là
resume toàn câu ở phía client, không phải resume-from-offset hay LiveKit. Không suy đoán
hỗ trợ tiếng Việt của model turn detector LiveKit khi chưa triển khai và chưa kiểm tra
danh sách ngôn ngữ của model cụ thể.

## Nguồn, khả năng tái kiểm tra và bước nghiên cứu tiếp

[Manifest nguồn](artifacts/consultation_voice_research_sources_20261101.json) lưu URL,
status, số byte/hash HTTP gốc và hash bản text trích xuất. PDF ALCE được trích bằng
pypdf của Python runtime có sẵn; lỗi thiếu pypdf ban đầu đã được sửa trong manifest.
Các bản text/PDF nằm ở `tmp/consultation_voice_research/`; trang động có thể thay đổi
khi tải lại nên cần dùng hash để phân biệt phiên bản.

Trước khi kỳ vọng “như nhân viên thật”, cần: dữ liệu nhãn đúng biến thể, người Việt/
nhân viên đánh giá mù các cuộc thoại hoàn chỉnh, tập câu nói retail tự nhiên có consent
và bản chép chính xác, ranh giới lượt nói có nhãn, cùng thiết bị micro/loa của kiosk.
Thay model/provider phải có ablation cố định dữ liệu và bộ lọc; không chọn vài đáp án
đẹp để công bố. Inventory provider hiện chỉ có ba model chat tổng quát đang dùng;
không âm thầm đổi sang model chuyên Arabic/safeguard hoặc mua dịch vụ khác.
