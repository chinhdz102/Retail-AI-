# Retail-AI — kế hoạch sửa lỗi và tối ưu dựa trên báo cáo (21/10/2026)

**Repository:** `chinhdz102/Retail-AI-`
**Baseline code:** `codex/retail-text-quality-v4-20261019` tại `4f9ff07`, so với `main` tại `ce8d3b5`.
**Nhánh sửa kiểm soát rủi ro:** `codex/retail-reliability-hardening-20261021`.
**Nguyên tắc:** Không dùng snapshot giả như tồn/giá cửa hàng thật; không gộp báo cáo test offline với đo chất lượng microphone hoặc provider live; không merge lên `main` trước acceptance.

## 1. Bằng chứng đã đọc và các giới hạn

- `docs/reports/retail-ai-optimization-report.md`: session, demo shopping, hỗ trợ/ticket, kết nối Postgres và Redis vẫn chưa được kiểm chứng trên server thật.
- `docs/reports/retail-ai-text-sales-flow-v3-report.md`: V4 held-out 23/49 assertion, 47/49 HTTP 200; điểm yếu là multi-intent, typo/slang, tham chiếu ngữ cảnh và sửa ý. Đây là tập đã dùng để chỉnh nhiều lần, không còn là kiểm thử mù. V5 từng có 12/50 assertion trong lần trước sửa và 50/50 ở một lần chạy development sau sửa; không được diễn giải thành tỷ lệ thành công thực địa. V6 snapshot-dev 50/50 assertions với chỉ 2 LLM calls trên 50 lượt.
- `evaluation/results/v6_verified_runtime_catalog_20261020.json`: 28 truy vấn trên snapshot, Recall@1/3/5 = 1.0; **PostgreSQL integration NOT_RUN**. Snapshot WinMart có 3.099 dòng, 3.081 được nhận và 18 quarantine; snapshot ngày 15/09/2026 không đảm bảo giá/tồn thời gian thực.
- `docs/reports/retail-ai-voice-silence-streaming-2026-10-20.md`: neural VAD Silero v5 local, 350 ms pre-roll, 12 s segment cap và STT cancellation được thêm trên feature; Groq Whisper vẫn nhận **segment hoàn tất**, TTS/agent không stream incremental. Chưa có field microphone WER/CER và end-of-speech-to-first-audio percentile.
- PR #1 merge vào nhánh feature, **không phải `main`**. Voice regression Actions trên SHA `c98498e` thành công; neural-browser-smoke cuối trên SHA đó bị **cancelled**, còn run trước thất bại. Không diễn giải cancelled là pass.
- `docs/reports/customer-care-flow-test-report-2026-10-19.md`: ticket/handoff, payment, checkout đang demo/in-memory, không có CRM/POS và thanh toán thật.

## 2. Đã sửa trong nhánh này

1. `backend/services/speech_tickets.py`: tránh ghi đè ticket TTS của người dùng khác nếu token factory trùng với token còn hiệu lực; giới hạn 16 lượt thử và thất bại an toàn, không đẩy ticket cũ khỏi cache khi không tạo được token mới; từ chối TTL hoặc sức chứa không hợp lệ.
2. `tests/test_speech_tickets.py`: regression cho hai khách cùng token, chuỗi collision kéo dài, TTL và cấu hình lỗi.
3. `.github/workflows/voice-regressions.yml`: chạy backend/frontend/TypeScript/build/lint trên `pull_request`, không chỉ push lên nhánh voice cố định.

**Phạm vi:** Hardening an toàn và chất lượng CI. Không tuyên bố đã tăng WER, giảm false wake-up hay latency. Cần xem kết quả CI trên HEAD của PR, không chỉ run cũ.

## 3. Ưu tiên theo mức độ ảnh hưởng

| Mức | Vấn đề | Công việc kỹ thuật cụ thể | Tiêu chí chấp nhận |
|---|---|---|---|
| P0 | Merge an toàn và lệch nhánh | Diff `main` ↔ feature; PR tích hợp sau acceptance; pin code + fixtures và thiết lập check trên PR | Tất cả job xanh trên đúng HEAD; không force merge |
| P0 | Phân biệt môi trường demo/real | Tag catalog source, timestamp, simulated shelf/stock ở từng phản hồi; từ chối giá/tồn live khi chưa có feed xác minh | 0 câu nói khẳng định đã thanh toán/hết hàng/còn hàng khi thiếu nguồn thật |
| P1 | Nhận đúng ý định và tham chiếu nhiều lượt | Củng cố DST SET/REPLACE/REMOVE/KEEP, entity resolver, query normalization và multi-intent trong cùng lượt; test slang/typo/đổi ý/thay sản phẩm | Benchmark **mù mới**, chấm state + hành động, không chỉ HTTP 200 |
| P1 | Mic im lặng, tiếng quạt, khách bên cạnh, tiếng loa Sky | Browser ONNX smoke thật; corpus audio có transcript chuẩn và nhãn thời điểm; A/B Silero + pre-roll với MediaRecorder RMS | Báo WER/CER, false activations/hour, missed short commands, first-syllable truncation, request count và p50/p95 E2E |
| P1 | Chờ phản hồi lâu và reset gây lỗi | Instrument từng mốc VAD-end → upload → STT → router/tool → LLM → TTS-first-byte → playback; abort, timeout, session owner | Không rò nội dung chéo session; không hồi sinh response cũ; latency theo p50/p95 mỗi stage |
| P2 | DB và dữ liệu vận hành | Chạy Postgres/Redis thật và probe tích hợp, xác định nguồn giá/tồn theo chi nhánh; bộ đồng bộ có timestamp/TTL/quarantine | Test tích hợp live đạt, có dữ liệu provenance và stale-data guard |
| P2 | Checkout và chuyển nhân viên | POS/CRM thật chỉ khi hợp đồng đã sẵn; idempotency, xác nhận trạng thái payment từ nguồn có thẩm quyền; persistence các ticket | Không làm giả PAID hoặc hứa nhân viên đã nhận khi chưa có event |
| P3 | Streaming/realtime và giao diện kiosk | Sau khi có benchmark, cân nhắc incremental STT + LLM + TTS; so sánh LiveKit với pipeline hiện tại; lazy-load avatar và assets VAD | A/B chứng minh giảm latency với độ chính xác và an toàn không giảm |

## 4. Quy trình đo và test để tránh overfit

### Sales text

- Tạo **ít nhất 100 hội thoại mới**, chia theo: tìm hàng, ngân sách, giỏ hàng, checkout, trả hàng/handoff, câu pha nhiều ý định, phương ngữ, typo và đổi ý giữa phiên; mỗi hội thoại 3–8 lượt. Không đặt dữ liệu này trong prompt/model tuning trước khi chốt bản ứng viên.
- Chấm 3 lớp: (1) grounding các trường dữ liệu; (2) state/cart/order và quyền hành động; (3) đánh giá tự nhiên bởi 2 người chấm độc lập.
- Xuất case ID, phiên bản catalog, commit SHA, model, latency p50/p95, HTTP, số LLM/tool calls, lỗi 429/5xx, tỷ lệ task success.
- Giữ bộ `v3/v4/v5/v6` cũ làm regression, tuyệt đối không gắn nhãn held-out mù mới.

### Voice kiosk

- Thử 10 phút mỗi điều kiện: yên tĩnh, quạt/điều hòa, nhạc cửa hàng, đàm thoại nền, tiếng loa Sky và ngắt giữa lúc trợ lý nói.
- Lấy mẫu câu tiếng Việt một/hai âm tiết và câu dài ở nhiều chất giọng, khoảng cách; người tham gia đồng ý thu thử và có transcript chuẩn đầy đủ.
- Ghi rõ tỷ lệ missed/false VAD, STT WER/CER có đối chiếu từ-nguyên-mẫu, latency end-of-speech tới first audio (p50/p95), thất bại tải WASM, bộ nhớ và thời gian khởi động nguội.
- Không commit file tiếng nói, transcript có dữ liệu cá nhân, API key hoặc thông tin khách.

### Integration + CI

- `uv run pytest -q`, `uv run ruff check backend tests evaluation`, `uv run python -m compileall -q backend tests evaluation`.
- `npm --prefix apps/kiosk-airi ci`, `npm --prefix apps/kiosk-airi run test:run`, `npm --prefix apps/kiosk-airi run build`.
- `evaluation/run_p0_regressions.py` và `run_shopping_flow_benchmark.py` với catalog demo cố định; không dùng latency in-process làm latency production.
- Chạy neural browser smoke trên kiosk Chrome/Edge với ONNX thực, rồi field micro. Đánh dấu riêng PASS / FAIL / NOT_RUN / UNSUPPORTED; không quy NOT_RUN thành PASS.

## 5. Thứ tự triển khai đề xuất

**Sprint 0 – hardening (nhánh hiện tại):** regression ticket, CI PR, tài liệu baseline; kiểm tra full suite CI.

**Sprint 1 – text quality:** viết blind set mới, phân tích trace theo nhóm lỗi, sửa DST và retrieval theo thứ tự tác động, A/B trên cùng bộ dữ liệu.

**Sprint 2 – voice qualification:** hoàn thiện real-browser ONNX smoke, ghi dataset âm thanh có đồng ý, kiểm tra failover fallback, đo latency thật theo stage; chỉ chỉnh VAD thresholds sau khi có số liệu.

**Sprint 3 – integration/production:** Postgres/Redis và feed chi nhánh, ticket/POS có backend bền nếu được phê duyệt, load test; sau đó quyết định có cần LiveKit/incremental STT/TTS.

## 6. Cổng merge

Chỉ đề xuất đưa vào `main` khi: CI trên commit cuối xanh; không có regression P0 hoặc rò session; các giới hạn demo hiển thị đúng; browser voice test và audio thật có kết quả kiểm chứng; phần production phụ thuộc feed/DB/POS chưa triển khai phải được bật/tắt bằng cấu hình rõ ràng. `main` không được cập nhật tự động trong kế hoạch này.
