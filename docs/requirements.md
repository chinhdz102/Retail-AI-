# AI Retail Assistant — MVP Requirements v1

**Trạng thái:** Frozen baseline  
**Ngày chốt:** 2026-08-31  
**Nguồn:** `AI_Retail_Full_Architecture_Research_Team_Plan_V4.md`  
**Phạm vi:** Bước 1 — Freeze Problem + Scope

## 1. Problem statement

Nhân viên cửa hàng tiện lợi thường xuyên phải trả lời các câu hỏi lặp lại về sản phẩm, giúp khách tìm vị trí hàng hóa, cung cấp thông tin, hỗ trợ lựa chọn và thực hiện các bước liên quan đến thanh toán. Trợ lý dựa trên LLM có thể tự động hóa một phần các tương tác này, nhưng không thể triển khai trực tiếp trong môi trường bán lẻ có giao dịch nếu chưa kiểm soát được ba rủi ro chính: thông tin sai, nhiễm dữ liệu giữa các phiên khách hàng và thực thi công cụ không an toàn.

Hệ thống cần chứng minh rằng LLM có thể đóng vai trò điều phối và giao tiếp, trong khi dữ liệu nghiệp vụ, quyền hành động và trạng thái thanh toán vẫn do các nguồn có thẩm quyền kiểm soát.

## 2. Product statement

Xây dựng một trợ lý bán lẻ hiện thân tại kiosk cho cửa hàng tiện lợi, giao tiếp bằng tiếng Việt, hỗ trợ khách tìm hiểu sản phẩm, nhận gợi ý, quản lý giỏ hàng và hoàn tất luồng thanh toán thử nghiệm. Mọi thông tin có tính giao dịch phải được truy xuất từ dữ liệu bán lẻ có thẩm quyền và mọi hành động quan trọng phải đi qua kiểm tra chính sách, xác nhận và validation.

Hệ thống hỗ trợ và tự động hóa một phần công việc của nhân viên; không tuyên bố thay thế hoàn toàn nhân viên cửa hàng.

## 3. Mục tiêu MVP

1. Cho phép khách tương tác bằng giọng nói tiếng Việt hoặc thao tác chạm dự phòng.
2. Trả lời đúng các dữ kiện thời gian thực gồm giá, tồn kho, vị trí kệ, khuyến mãi và hạn dùng.
3. Trả lời kiến thức sản phẩm dựa trên tài liệu đã được phê duyệt.
4. Đề xuất sản phẩm thỏa toàn bộ ràng buộc rõ ràng của khách.
5. Quản lý giỏ hàng, xác nhận checkout và hiển thị QR thanh toán sandbox.
6. Chỉ công nhận thanh toán từ callback hoặc kiểm tra backend của nhà cung cấp.
7. Cô lập hoàn toàn dữ liệu tương tác giữa các shopping session.
8. Từ chối hành động vượt quyền hoặc không phục vụ mục tiêu hiện tại của khách.
9. Có đường chuyển tiếp cho nhân viên khi hệ thống thiếu bằng chứng hoặc không thể xử lý an toàn.
10. Thu thập được số liệu phục vụ đánh giá học thuật và so sánh baseline.

## 4. Phạm vi đã chốt

### 4.1. Ràng buộc vận hành

- Một kiosk demo.
- Khách hàng ẩn danh theo mặc định.
- Mỗi lượt mua sắm là một session độc lập, có thời hạn.
- Catalog demo gồm 100–200 SKU.
- Giao tiếp chính bằng tiếng Việt.
- Thanh toán sử dụng VietQR sandbox/test; không thực hiện giao dịch tiền thật trong MVP.
- AI phía khách hàng không có quyền quản trị.
- FastAPI được thiết kế stateless; state phiên nằm ngoài process ứng dụng.

### 4.2. Chức năng thuộc MVP

#### P0 — Bắt buộc cho luồng demo chính

- AIRI avatar/kiosk và trạng thái listening, thinking, speaking.
- Nhập giọng nói qua microphone và Vietnamese STT.
- Agent Qwen hỗ trợ structured output và function calling.
- Shopping session tạm thời, được cô lập.
- Tìm và phân giải sản phẩm.
- Product snapshot: giá, tồn kho, kệ và khuyến mãi.
- Tra cứu kiến thức sản phẩm có grounding.
- Tra cứu batch/hạn dùng.
- Gợi ý sản phẩm theo ràng buộc.
- Xem, thêm, sửa và xóa mặt hàng trong giỏ.
- Xác nhận checkout trước khi tạo đơn.
- Hiển thị QR thanh toán sandbox và kiểm tra trạng thái thanh toán.
- Policy gateway và response validation cho hành động/dữ kiện quan trọng.

#### P1 — Bắt buộc trước khi nghiệm thu MVP hoàn chỉnh

- Chuyển tiếp cho nhân viên.
- Báo cáo vấn đề sản phẩm.
- Dashboard owner/admin tối thiểu với xác thực và phân quyền riêng.
- Logging có cấu trúc phục vụ debug và evaluation.
- Bộ đánh giá baseline, kiểm thử red-team và báo cáo metric.

### 4.3. Ngoài phạm vi MVP

- Nhận diện khuôn mặt.
- Nhận diện người nói phức tạp.
- Persistent memory cho khách ẩn danh.
- Dùng chung lịch sử hội thoại giữa nhiều khách.
- Kiến trúc nhiều LLM agent.
- GraphRAG.
- Full-vision self-checkout.
- Hồ sơ hóa khách hàng bí mật.
- Dynamic pricing do AI tự quyết định.
- AI tự sửa giá, tồn kho hoặc hạn dùng.
- AI tự đánh dấu thanh toán là `PAID`.
- Customer agent thực hiện thao tác quản trị tự động.

## 5. Actor và hệ thống bên ngoài

| Actor | Vai trò |
|---|---|
| Customer | Hỏi thông tin, nhận gợi ý, quản lý giỏ và thanh toán tại kiosk. |
| Store Staff | Nhận handoff hoặc xử lý vấn đề mà hệ thống không thể giải quyết an toàn. |
| Owner/Admin | Xem dashboard tối thiểu qua cơ chế xác thực và bộ quyền tách biệt. |
| Payment Provider | Tạo/đối soát giao dịch sandbox và cung cấp trạng thái thanh toán có thẩm quyền. |
| Approved Data Maintainer | Chuẩn bị catalog, tồn kho, batch và tài liệu kiến thức đã phê duyệt trước demo. |

## 6. Nguồn sự thật có thẩm quyền

| Loại dữ liệu | Nguồn sự thật | LLM được phép làm gì |
|---|---|---|
| Giá, tồn kho, kệ, khuyến mãi, batch, hạn dùng | PostgreSQL qua structured tools | Chọn tool, tạo tham số và diễn đạt kết quả. |
| Thành phần, dinh dưỡng, dị ứng, mô tả, chính sách | Tài liệu đã phê duyệt qua hybrid RAG | Tóm tắt bằng evidence được truy xuất. |
| Session và giỏ tạm thời | Redis/session service | Đọc state liên quan qua interface được phép. |
| Tổng tiền và trạng thái đơn | Cart/order backend | Giải thích; không tự tính kết quả cuối cùng. |
| Trạng thái thanh toán | Callback hoặc backend verification của provider | Yêu cầu kiểm tra và thông báo kết quả. |
| Quyền hành động | Policy gateway và backend authorization | Không tự cấp hoặc mở rộng quyền. |

## 7. Mười use case cốt lõi

### UC-01 — Hỏi giá

- Customer chọn hoặc mô tả một sản phẩm.
- Hệ thống phân giải đúng SKU; nếu có nhiều kết quả, yêu cầu chọn lại.
- Giá hiện hành được lấy từ structured tool.
- Kết quả hiển thị/nói đúng tên sản phẩm, đơn vị tiền và thời điểm dữ liệu phù hợp.

### UC-02 — Hỏi vị trí kệ

- Hệ thống phân giải SKU và lấy vị trí kệ từ dữ liệu nghiệp vụ.
- Nếu chưa có vị trí, hệ thống nói rõ chưa xác minh và đề nghị handoff; không tự bịa vị trí.

### UC-03 — Hỏi tồn kho

- Hệ thống trả trạng thái/số lượng theo dữ liệu tồn kho hiện hành.
- Kết quả không được suy ra từ tài liệu RAG hoặc lời nói của khách.

### UC-04 — Hỏi hạn dùng

- Hệ thống lấy thông tin batch/hạn dùng theo FEFO khi truy vấn không chỉ rõ batch.
- Nếu không xác định được sản phẩm hoặc batch, hệ thống hỏi lại hoặc chuyển nhân viên.

### UC-05 — Hỏi thành phần sản phẩm

- Hệ thống truy xuất tài liệu sản phẩm đã được phê duyệt.
- Câu trả lời chỉ chứa claim được evidence hỗ trợ.
- Khi evidence yếu hoặc thiếu, hệ thống thông báo không xác minh được.

### UC-06 — Gợi ý theo ràng buộc

- Customer đưa một hay nhiều tiêu chí, ví dụ loại hàng, ngân sách, độ cay và tình trạng còn hàng.
- Backend tạo tập ứng viên thỏa điều kiện cứng.
- Hệ thống kiểm tra từng ràng buộc trước khi trả kết quả và giải thích lý do gợi ý.

### UC-07 — Thêm vào giỏ

- Hệ thống phân giải SKU, kiểm tra số lượng hợp lệ và tồn kho.
- Customer xác nhận khi yêu cầu còn mơ hồ hoặc có tác động đáng kể.
- Cart backend cập nhật giỏ; LLM không tự ghi dữ liệu.

### UC-08 — Xóa hoặc giảm số lượng trong giỏ

- Hệ thống chỉ thay đổi giỏ thuộc session hiện tại.
- Số lượng không được âm và thao tác lặp phải cho kết quả nhất quán.
- Giỏ sau cập nhật được đọc lại từ backend.

### UC-09 — Checkout

- Backend kiểm tra lại giá, tồn kho, subtotal và total.
- Customer nhìn thấy tóm tắt đơn và xác nhận rõ ràng trước khi tạo checkout.
- Hệ thống không dựa vào phép tính của LLM làm số tiền cuối cùng.

### UC-10 — Xác nhận thanh toán

- Hệ thống tạo/hiển thị QR sandbox gắn với checkout hiện tại.
- Câu nói “tôi đã trả tiền” chỉ kích hoạt kiểm tra trạng thái.
- Chỉ callback hợp lệ hoặc backend verification mới có thể chuyển trạng thái sang `PAID`.
- Callback trùng lặp không tạo giao dịch hoặc cập nhật thanh toán trùng.

## 8. Functional requirements

| ID | Yêu cầu |
|---|---|
| FR-001 | Hệ thống phải tạo định danh session không đoán được cho mỗi shopping episode và áp dụng TTL. |
| FR-002 | Hệ thống phải đóng session khi checkout hoàn tất, khách chủ động kết thúc hoặc session hết hạn. |
| FR-003 | Mọi thao tác cart, order và context phải được giới hạn theo session hiện tại. |
| FR-004 | Hệ thống phải hỗ trợ nhập giọng nói tiếng Việt và có đường nhập chạm/text khi STT thất bại. |
| FR-005 | Hệ thống phải phân giải barcode, product ID hoặc tên sản phẩm thành SKU trước khi gọi tool phụ thuộc sản phẩm. |
| FR-006 | Giá, tồn kho, kệ, khuyến mãi và hạn dùng phải được lấy qua structured tools từ dữ liệu nghiệp vụ. |
| FR-007 | Kiến thức sản phẩm phải được lấy từ corpus đã phê duyệt và trả kèm provenance nội bộ. |
| FR-008 | Recommendation phải kiểm tra riêng từng constraint do customer nêu rõ. |
| FR-009 | Mọi tool call phải qua schema validation, role check, state check, goal alignment và business rules. |
| FR-010 | Hành động tạo checkout phải yêu cầu xác nhận rõ ràng của customer. |
| FR-011 | Cart/order backend phải tính giá, subtotal và total; giá trị do LLM tạo không có hiệu lực giao dịch. |
| FR-012 | Trạng thái `PAID` chỉ được cập nhật từ callback đã xác thực hoặc backend verification. |
| FR-013 | Payment callback và create-checkout phải hỗ trợ idempotency. |
| FR-014 | Response validator phải kiểm tra các claim quan trọng về số, sản phẩm và ràng buộc intent trước khi trả lời. |
| FR-015 | Khi validation thất bại, hệ thống phải retry có giới hạn, abstain hoặc handoff; không được âm thầm trả claim chưa xác minh. |
| FR-016 | Customer agent không được expose tool quản trị hoặc raw SQL. |
| FR-017 | Hệ thống phải ghi structured log cho request ID, session ID giả danh, intent, tool, kết quả policy, validation và latency; không ghi secret. |
| FR-018 | Owner/admin dashboard phải dùng authentication và RBAC tách biệt với customer agent. |
| FR-019 | Hệ thống phải hỗ trợ handoff và báo cáo vấn đề sản phẩm trong session hiện tại. |
| FR-020 | Hệ thống phải cung cấp dữ liệu đầu ra để đánh giá factuality, tool calling, RAG, session isolation, security và performance. |

## 9. Non-functional requirements

### NFR-SEC — Security

- NFR-SEC-001: Mọi input từ voice, text, barcode, image và touch được xem là không tin cậy.
- NFR-SEC-002: Tool registry của customer agent là static và chỉ expose tool cần cho intent hiện tại.
- NFR-SEC-003: Secret không được commit, trả về API response hoặc xuất hiện trong log.
- NFR-SEC-004: Rate limit được áp dụng cho API và tool có khả năng gây tải hoặc tác động nghiệp vụ.
- NFR-SEC-005: Critical Tool Misuse Rate, Cross-session Leakage Rate và False Payment Confirmation Rate phải bằng 0 trên bộ kiểm thử nghiệm thu đã chốt.

### NFR-REL — Reliability

- NFR-REL-001: FastAPI không phụ thuộc vào in-process session state.
- NFR-REL-002: Redis hỏng không được làm các phiên trộn dữ liệu; hệ thống phải fail closed hoặc dùng fallback cô lập đã cấu hình rõ cho demo.
- NFR-REL-003: Khi Qwen không khả dụng, agent phải báo lỗi provider và không được gắn nhãn Qwen cho câu trả lời deterministic; các thao tác catalog read-only vẫn có thể hoạt động.
- NFR-REL-004: Demo phải chạy ổn định liên tục trong 20 phút với luồng chính và không cần khởi động lại thủ công.

### NFR-PERF — Performance và observability

- NFR-PERF-001: Hệ thống phải đo và báo cáo latency p50/p95 cho API, tool, STT, TTS và end-to-end response.
- NFR-PERF-002: Mỗi request phải có correlation/request ID xuyên suốt các service thuộc phạm vi MVP.
- NFR-PERF-003: Ngưỡng latency dùng để so sánh các baseline, không phải release gate của bước Freeze Requirements vì phụ thuộc hạ tầng/model được chọn ở giai đoạn feasibility.

### NFR-PRIV — Privacy

- NFR-PRIV-001: Khách ẩn danh không có persistent conversational memory.
- NFR-PRIV-002: Dữ liệu session hết hạn phải được xóa theo TTL hoặc explicit close.
- NFR-PRIV-003: Customer không thể truy vấn nội dung phiên trước hoặc phiên đang hoạt động trên kiosk khác.

### NFR-TEST — Testability

- NFR-TEST-001: Mỗi feature phải có unit test, integration test, schema validation, error handling, security condition, logging và tài liệu API liên quan.
- NFR-TEST-002: Các nguồn dữ liệu demo và evaluation dataset phải có phiên bản để tái lập kết quả.

## 10. Acceptance criteria của bước 1

### 10.1. Traceability tối thiểu

| Use case | Functional requirements chính |
|---|---|
| UC-01 — Hỏi giá | FR-005, FR-006, FR-014, FR-015 |
| UC-02 — Hỏi vị trí kệ | FR-005, FR-006, FR-014, FR-015 |
| UC-03 — Hỏi tồn kho | FR-005, FR-006, FR-014 |
| UC-04 — Hỏi hạn dùng | FR-005, FR-006, FR-014, FR-015 |
| UC-05 — Hỏi thành phần | FR-007, FR-014, FR-015 |
| UC-06 — Gợi ý theo ràng buộc | FR-005, FR-008, FR-014, FR-015 |
| UC-07 — Thêm vào giỏ | FR-003, FR-005, FR-009, FR-017 |
| UC-08 — Xóa/giảm trong giỏ | FR-003, FR-009, FR-017 |
| UC-09 — Checkout | FR-009, FR-010, FR-011, FR-013 |
| UC-10 — Xác nhận thanh toán | FR-012, FR-013, FR-017 |

### 10.2. Exit criteria

Bước Freeze Requirements hoàn thành khi:

- Problem statement và product statement được chốt.
- Phạm vi P0, P1 và ngoài phạm vi không mâu thuẫn.
- 10 use case cốt lõi có outcome và hành vi lỗi rõ ràng.
- Nguồn sự thật cho dữ liệu nghiệp vụ, RAG, session và payment được xác định.
- Quyền của Customer Agent và Owner/Admin được tách rõ.
- Các yêu cầu có ID để truy vết sang kiến trúc, API, test và evaluation ở bước sau.
- Không còn quyết định mở ảnh hưởng đến việc lập ERD, tool contract hoặc API contract.

## 11. Tiêu chí thành công của MVP

### Functional

- Customer nói tiếng Việt và hệ thống hiểu yêu cầu trong bộ test demo.
- Product lookup trả đúng SKU; giá, tồn kho và hạn dùng khớp dữ liệu nghiệp vụ.
- Recommendation thỏa mọi constraint được nêu rõ.
- Cart, checkout, QR và payment-status flow hoạt động đúng end-to-end.

### Reliability và security

- Session A không làm lộ hoặc ảnh hưởng session B.
- Hệ thống không tạo product-knowledge claim khi thiếu evidence.
- Qwen không thể tự sửa business data hoặc tự xác nhận thanh toán.
- Prompt injection không dẫn đến high-impact tool call trái phép.
- Customer không thể leo thang sang quyền admin.

### Research

- Có thể chạy và so sánh bốn cấu hình: Qwen-only, Qwen + RAG, Qwen + RAG + Tools, và hệ thống đề xuất có session/policy/validation.
- Hệ thống xuất được metric về factuality, hallucination, tool calling, constraint satisfaction, retrieval, session isolation, latency và security.

## 12. Quy tắc quản lý thay đổi

Tài liệu này là baseline v1. Mọi thay đổi phạm vi sau khi chốt phải:

1. Nêu requirement ID hoặc use case bị ảnh hưởng.
2. Mô tả tác động đến ERD, tool contract, API, test, lịch triển khai và evaluation.
3. Tăng phiên bản tài liệu sau khi nhóm duyệt.

Các lựa chọn implementation cụ thể chưa cần cho việc đóng băng yêu cầu — như cấu trúc bảng chi tiết, JSON schema, endpoint, provider key và deployment topology — được quyết định ở các bước kiến trúc tiếp theo, miễn không vi phạm baseline này.
