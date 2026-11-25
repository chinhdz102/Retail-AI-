# Kế hoạch tiếp tục kiểm tra tư vấn — 03/11/2026

Mục tiêu: khách nhận được hướng chọn có lý do phù hợp với việc cần làm; có thể
đổi ý, nhờ quyết định, bổ sung tiêu chí và chuyển nhóm hàng mà cuộc trò chuyện
không mất mục đích. Kiến thức mua sắm thông thường được dùng để tư vấn; thông tin
của từng mã hàng vẫn cần bằng chứng. Không dùng bảng câu hỏi → đáp án/SKU.

1. Giữ baseline: regression toàn backend và replay API thật trước sửa.
2. Đối chiếu ChatCRS, PC-CRS, ProductAgent, UAI 2024 và PEPPER; ghi rõ kỹ thuật áp
   dụng, điều chỉ mới là đề xuất, và giới hạn đánh giá bằng dữ liệu đã lộ.
3. Sửa vòng đời lượt tư vấn: checkpoint sau kế hoạch hợp lệ; lỗi provider không
   lọt xuống tìm tên hàng; không xuất bản sản phẩm chưa được kiểm tra; giữ lời
   khách và ràng buộc để tiếp tục.
4. Rà policy lựa chọn/hỏi thêm, nội dung dài dòng, thông tin không có cơ sở và
   câu trả lời chung chung. Kiểm tra nhiều nhóm hàng, ngân sách, đổi ý và chuyển
   mục đích. Ưu tiên tìm nguyên nhân chung thay vì thêm ngoại lệ cho từng câu.
5. Chạy regression, replay 35 câu cùng các journey nhiều lượt và kiểm tra giao
   diện demo. Đọc đáp án thực tế; HTTP 200, số thẻ hàng và test phần mềm không
   phải điểm giống nhân viên. Báo cáo riêng quota/lỗi và nội dung chưa đạt.

Baseline: `1817 passed, 1 skipped, 1 warning` trước sửa của đợt này. Bỏ qua một
test symlink do quyền Windows; warning là Starlette/httpx deprecation. Replay
skin-care trước sửa vẫn có lời khuyên vô nghĩa và một fallback; xem artifact
`retail_advice_continuation_before_20261102.json`. Không coi baseline đã đạt.

Voice/STT/TTS/VAD: dùng kết quả đo và giới hạn đã có, kiểm tra regression khi
thay đổi dùng chung. LiveKit chưa được tích hợp; không gọi readiness là chạy thật.

## Cập nhật thực hiện trong đợt 03/11

- [x] Đọc paper gốc về goal planning, critique/refinement, product clarification,
  target-free evaluation; đối chiếu thêm dữ liệu hội thoại dịch vụ thật, ESCI và
  MG-ShopDial. Không tuyên bố đã huấn luyện/tái lập kết quả các paper.
- [x] Sửa nullable contract, giới hạn critique, giữ request qua outage, giữ ràng
  buộc theo vai trò, phạm vi taxonomy và ngữ cảnh từ khóa tiếng Việt có dấu.
- [x] Loại bỏ ép mọi số đo thành quy cách gói; giữ quy cách ở lượt thử lại bằng
  ngữ pháp gói/chai/dung tích rõ và đối chiếu kế hoạch ngữ nghĩa.
- [x] Audit toàn 3.081 sản phẩm / 346 đường dẫn; 12.357 kiểm tra truy xuất/ràng
  buộc qua. Không coi đây là kiểm tra ngữ nghĩa của 3.081 cuộc hội thoại.
- [x] Replay 35 câu + journey; tự đọc 62 lượt đã ghi, đánh dấu rõ phiên bản,
  lỗi khả dụng và câu tư vấn yếu. Có nhận xét riêng cho từng câu người dùng.
- [x] Frontend 102 test và build qua; scoped backend 279 test qua; full regression
  cuối 1.871 passed / 1 skipped sau sửa regression quy cách 500ml. Replay offline
  provider lỗi trên source cuối: 54/54 SSE hoàn chỉnh, 9 lượt hỏi tiếp không lọt hàng.
- [x] Vòng v7 loại `revised_text` chưa được duyệt khỏi retry, chặn cách viết
  alternatives thành giỏ mua chung, kiểm tra 463 nhãn/346 đường dẫn taxonomy và
  bổ sung hash `retail_tools.py` vào evaluator.
- [x] Full backend sau vòng v7: **1.989 passed / 1 skipped / 1 warning** trong
  380,26 giây; Ruff, compileall và `git diff --check` qua.
- [ ] Chưa có replay ngữ nghĩa thành công đủ rộng với quota ổn định; chưa chấm
  mù bởi nhân viên thật. Lượt cuối 2 câu yếu và 6 fallback không đạt mục tiêu.
- [ ] Chưa có bộ dữ liệu Việt có nhãn query–product/hội thoại nhân viên, ablation
  reranker hoặc preference tuning. Đây là hướng tiếp theo, chưa thực hiện.
- [ ] Chưa nghiệm thu micro/loa thật hoặc tích hợp LiveKit runtime.

Voice continuation 03/11: [voice/STT/TTS optimization report](voice_optimization_20261103.md)
đã đọc thêm paper Whisper hallucination, Calm-Whisper, ViMD, window-size VAD và
incremental TTS, thêm paper chuẩn hóa từ tiếng Việt cho TTS. Đã sửa race cleanup VAD,
stale playback/language switch, STT metadata guard; triển khai Silero v5 server cho
cả WAV/WebM/MP4/MP3, giới hạn CPU/duration và chuẩn hóa tiền/đơn vị đọc bằng Azure.
Frontend **115 test / 21 suites**, scoped voice backend **148 test**, full backend
**2.057 passed / 1 skipped / 1 warning**; build/Ruff/compile/diff-check qua.

Replay browser dùng 22 clip người thật và 5 clip môi trường. Replay server 84 lượt
(có biến thể tương quan): **46 speech được giữ, 34 controls bị chặn, 4 input errors
do audio >60 s**, không gộp lỗi input thành pass. API STT thật sau restart chặn cả
5 ESC controls; 3 ViMD speech đều được giữ nhưng WER lần lượt 33,33% / 0% / 15%.
TTS ticket/stream/decode thật qua 3 locale; first audio vi 1.876 ms, en 178 ms,
zh 240 ms, mỗi locale một lần. Không dùng số đo nhỏ này làm benchmark p95/MOS.
Micro/loa phòng, endpoint labels, MOS TTS và LiveKit vẫn chưa nghiệm thu.

Báo cáo chính: [chất lượng tư vấn](consultation_human_quality_20261103.md).
Đánh giá từng lượt: [manual review](consultation_human_manual_review_20261103.md).
Nhật ký nghiên cứu và thay đổi v7: [employee iteration](consultation_employee_iteration_20261103.md).

Kiểm tra demo sau restart: frontend `5173` và backend `/health` đều 200; silence
WAV mới trả rỗng trước Whisper; ba artifact live voice khớp source hash hiện tại.
Chi tiết ở [delivery smoke](artifacts/voice_acoustic_delivery_20261103.json).

