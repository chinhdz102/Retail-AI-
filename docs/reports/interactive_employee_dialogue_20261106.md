# Kiểm thử hội thoại bán hàng ngày 06/11/2026

Trạng thái: **chưa đạt chất lượng nhân viên**. Đã lưu các vòng test thật và sửa nhiều cơ chế gây lỗi; chưa có bằng chứng chấp nhận được mục tiêu tư vấn tự nhiên, hữu ích và ổn định. Các số test phần mềm không phải điểm tư vấn.

Phạm vi là hội thoại text thật qua demo và HTTP API dùng provider đang cấu hình. Không có lần nói vào mic vật lý, không chấm lại STT/TTS/VAD/LiveKit trong đợt này. Kiosk đang tập trung tiếng Việt theo yêu cầu trước.

## Tiêu chí và cách kiểm tra

Bộ [11 lượt, 6 hành trình](../../evaluation/cases/interactive_dialogue_20261106.json) được khai báo trước khi chạy: đồ dọn nhà, ăn sáng với thiết bị hạn chế, sổ ghi chi tiêu, đồ uống với loại trừ tích lũy, thịt ăn lẩu và cách chọn chất vệ sinh quanh mèo. Có đổi ngân sách, đổi loại, nhờ chọn một món và yêu cầu không chốt hãng. Không có SKU đáp án cố định.

Mỗi lượt kiểm: hiểu mục đích; giữ ngân sách/điều loại trừ/thiết bị; chọn giúp hoặc đưa hướng thực tế có lý do; chỉ hỏi thông tin thật sự cần; không bịa công dụng, cảm giác, lịch sử hay tồn kho. Chấm đạt/yếu/sai/lỗi dịch vụ bằng đọc thủ công của Codex. Đây là tập phát triển đã lộ và người xây hệ thống tự chấm, chưa phải người bán/người dùng độc lập. Không suy tỷ lệ chất lượng tổng quát từ mẫu nhỏ.

Các capture HTTP cách ít nhất 45 giây sau mỗi phản hồi, lưu source hashes, giữ cả fallback trong mẫu số. Nguồn production không sửa giữa một capture. Backend được restart trước mỗi bản HTTP. Các trace gọi lõi trực tiếp dùng để tìm nguyên nhân, được tách khỏi kiểm tra giao diện và HTTP.

## Lỗi quan sát được và sửa cơ chế

1. **LLM hiểu đúng nhưng code ghi đè sai:** trace planner đặt `basket=true`, tổng 100.000đ và hai vai trò rửa chén/lau bếp. Parser lexical chỉ nhận ra nhóm đầu; guard cho một loại đã đổi thành `basket=false`, xóa tổng. Đã chặn guard này khi kế hoạch có nhiều nhóm chức năng khác nhau. Giữ cơ chế chống giỏ hàng do model tự bịa ở yêu cầu một loại thật sự.
2. **Câu mở hàng bị ép theo khuôn:** cho LLM đặt `{product}` trong câu tự viết; ứng dụng gắn tên/giá/số lượng. Không thêm câu trả lời cố định cho ngành hàng. Tương thích mảnh cũ được giữ.
3. **Reviewer bác chức năng cơ bản nhưng lại lọt lợi ích bịa:** phân biệt nghĩa của loại hàng với hiệu năng/sức khỏe/an toàn; thêm kiểm mục tiêu toàn câu trả lời bên cạnh từng câu. Các cờ `answers_current_request`, `preserves_constraints`, `advances_decision` đều phải có; thiếu/sai kiểu/bác mà không lý do không tự được đổi thành chấp nhận. Bộ duyệt vẫn có thể chấm sai về ngữ nghĩa; số liệu thật bên dưới thể hiện rõ giới hạn.
4. **Lời hứa đủ dùng:** chặn lời khẳng định đủ dùng/đủ người ngoài bằng chứng, kể cả reviewer cho qua. Lời khuyên chọn và chuẩn bị thông thường vẫn được giữ.
5. **Lặp dữ kiện và sai giá:** có lần model gõ lại đúng tên/giá thay vì marker, dẫn tới câu giới thiệu lặp và renderer hủy lượt. Cho phép gắn lại tên/giá **khớp nguyên dữ kiện** (chỉ linh hoạt khoảng trắng), không sửa gần đúng tên/quy cách/giá sai. Test thực tế sau đó tìm thấy `1 × 1 ×` và giá làm tròn `31 kđ` cạnh giá 30.800đ; thêm kiểm đơn vị tiền ghép và chỉ bỏ số lượng lặp khi đúng lượng đã chọn.
6. **Một mục đích biến thành quá nhiều món bắt buộc:** trường `optional` được mô tả theo vai trò do khách cần; món phụ do model tự thêm phải để optional nếu không bắt buộc. Chưa coi một mô tả schema là bằng chứng lỗi đã hết.
7. **Lời tư vấn chung bị viết lại thừa:** với lượt chỉ hỏi cách chọn và không có câu hỏi chờ, hướng do planner viết được render/kiểm độc lập trước. Nếu đạt, trả chính bản đã duyệt, tiết kiệm một lần sinh; nếu bị bác vẫn qua bước viết/sửa và kiểm độc lập. Không xuất planner text chưa duyệt.
8. **Câu hỏi tiếp nối đi vòng qua review:** câu clarify của runtime khi đã có nhóm hàng/mục đích tiếp tục qua đường viết lời hướng dẫn và duyệt, để không chỉ hỏi lại khách muốn loại nào. Chưa coi điều này bảo đảm mọi câu hỏi đều hữu ích.
9. **Dùng sai thiết bị và tự hứa vị ngọt:** một câu thật đề xuất nấu phở trong ấm đun nước, nhanh và không quá ngọt. Đã thêm nguồn hướng dẫn thiết bị và chặn lời hứa vị ngọt từ SKU không có chứng cứ. Không tạo công thức hay mapping câu ăn sáng sang món cụ thể.

## Kết quả đã ghi nhận

| Capture | Lượt | Đạt | Yếu | Sai | Lỗi dịch vụ |
| --- | ---: | ---: | ---: | ---: | ---: |
| HTTP v1 sau sửa mua cùng | 11 | 0 | 0 | 2 | 9 |
| HTTP v2 tập trung đồ dọn nhà + mèo | 3 | 0 | 1 | 1 | 1 |
| HTTP v3 đủ 6 hành trình | 11 | 0 | 1 | 4 | 6 |
| HTTP v4 sổ + đồ uống + thịt | 6 | 0 | 0 | 4 | 2 |
| HTTP v5 sau sửa ý định mua và coverage | 6 | 0 | 0 | 0 | 6 |

V1: [raw](artifacts/interactive_employee_http_20261106_v1.json), [bản chấm](artifacts/interactive_employee_http_20261106_v1_manual.json). Hai câu có nội dung là câu phở/ấm đun nước sai ở trên, và câu hỏi lại loại nước dù khách đã có mục đích/điều kiện. V2: [raw](artifacts/interactive_employee_http_20261106_v2_focus.json), [bản chấm](artifacts/interactive_employee_http_20261106_v2_focus_manual.json). Lượt đổi sang chỉ mua nước rửa chén dưới40 nghìn giữ được ngân sách và chọn loại trà xanh; lý do còn quá chung. Hai tập khác phạm vi, không so thống kê thành phần trăm cải thiện.

Giao diện trước sửa mua cùng có bốn fallback trong hai lần thử cùng hành trình, tách hai artifact: [trước natural binding](artifacts/interactive_dialogue_ui_before_20261106.json), [sau natural binding](artifacts/interactive_dialogue_ui_natural_20261106.json). Một direct-core trace trả được câu không chứng minh UI đã thành công. [Trace planner/renderer](artifacts/interactive_household_natural_trace_20261106.json), [trace giỏ nhiều vai trò](artifacts/interactive_joint_diagnostic_20261106.json) được giữ để audit nguyên nhân. Trace thứ hai bật Python exception tracing, không dùng đo latency.

## Sửa tiếp sau khi đọc từng câu trả lời

- **Lời tư vấn chung lọt loại trừ:** trước đây `excluded` chỉ lọc title; lời nói vẫn có thể gợi ý “lò co” sau “không lò xo”. Dùng chung matcher descriptor/alias vật lý cho prose, giữ các cảnh báo phủ định ở từng mệnh đề. Test cả đồ uống, thịt, sổ, dạng refill; kiểm cả đường planner guidance và phục hồi. Đây là kiểm literal/alias, chưa phải suy luận ngữ nghĩa đầy đủ.
- **Ví dụ prompt thành câu trả lời:** v3 model sao chép nguyên “trước để giải quyết việc bạn cần; món phụ có thể mua sau”. Bỏ câu ví dụ có thể dùng làm lời bán hàng; giữ ký hiệu định dạng để LLM tự viết lý do.
- **Mất nhiều ý phủ định trong một câu:** parser từng bỏ “không B” trong “không A và không B”, bỏ “đừng + danh từ” và “X thì bỏ”. Sửa cách tìm mệnh đề, giữ dấu tiếng Việt và test đối chứng với “dùng”, “Dung dịch”, “bỏ vào giỏ”, chỉ dẫn “đừng chỉ liệt kê tên”. Full test phát hiện hồi quy nhầm `Dung dịch` thành `đừng`; đã sửa và kiểm lại toàn bộ taxonomy.
- **Ý định mua thành lời hướng dẫn:** phân biệt yêu cầu mua/chọn giúp với hỏi cách chọn/dùng, trong một phạm vi hàng đã xác định. Không tự ép SKU cho câu hỏi cách chọn, điều kiện an toàn, trẻ em hoặc khi cần xác minh nhãn. Việc này chỉ sửa dialogue act, không tự chọn sản phẩm.
- **Ngân sách biến thành giá thị trường:** lời “cả hai loại thường trong 20 nghìn” từng qua vì trùng con số ngân sách. Chặn cách dùng đó; tên/giá SKU vẫn do app gắn. Khách vẫn có thể nhận lời chọn trong ngân sách của chính họ.
- **Coverage cho bước tìm lại bỏ quên loại trừ:** nhóm có hàng nhưng mọi hàng hợp giá đều bị loại vẫn được quảng bá là nhóm có thể chọn. Coverage mới giữ loại trừ khi đưa các nhóm khả thi cho LLM chọn lại. Không gắn câu hỏi vào SKU hay tự chọn ngành thay thế.
- **Khoanh phạm vi theo chính nhóm bị loại:** với yêu cầu đồ uống không nước ngọt có ga, helper đã khoanh chỉ12 leaf, không có nhóm nước khoáng. Bỏ hint nếu nó là nhóm bị khách loại, giữ taxonomy rộng cho LLM suy luận. Test tương tự với đồ uống, đèn, sổ/vở. Sửa này **sau capture v5**, chỉ được xác nhận phần mềm và kiểm UI cuối; không gán ngược cho v5.
- **Mất câu nhập khi phiên không còn:** quan sát được trên demo sau restart backend. Frontend nay giữ đúng câu text vừa bị từ chối trong ô nhập để khách gửi lại. Không tự thực hiện lại thao tác, không phát lại audio, không mang lịch sử/giỏ phiên cũ sang khách khác. Test cả reset chủ động, phản hồi muộn và voice expiry.

V3: [raw](artifacts/interactive_employee_http_20261106_v3_final.json), [chấm](artifacts/interactive_employee_http_20261106_v3_final_manual.json). V4: [raw](artifacts/interactive_employee_http_20261106_v4_constraints.json), [chấm](artifacts/interactive_employee_http_20261106_v4_constraints_manual.json). V5: [raw](artifacts/interactive_employee_http_20261106_v5_purchase.json), [chấm](artifacts/interactive_employee_http_20261106_v5_purchase_manual.json). Các capture đều giữ source hash ổn định trong lượt; bản chấm không ghi đè raw. Cộng37 lượt HTTP lặp trên tập phát triển: **0 đạt, 2 yếu, 11 sai, 24 lỗi dịch vụ**. Không phải37 câu mới độc lập; không dùng để suy chất lượng ngoài mẫu.

V3 có gà viên sau yêu cầu bỏ viên; v4 chọn thịt xay và tự hứa nấu nhanh/dễ chín; reviewer vẫn duyệt sai quan hệ bằng chứng. V5 chặn được nhiều lời không đủ căn cứ nhưng cả6 lượt thành fallback: không được gọi đây là tăng chất lượng tư vấn. Log có `structured-validation` và429/cooldown; không đủ dữ kiện để quy mỗi lượt hoàn toàn cho quota. [Trace một lượt thịt](artifacts/interactive_exclusion_diagnostic_before_20261106.json) cho thấy Qwen chọn,120B bác lợi ích/khẩu phần,20B sửa rồi20B tự chấp nhận “nấu nhanh” từ dữ kiện type/uom. Đây là lỗi ngữ nghĩa thật bên cạnh hạn mức dịch vụ. Trace dừng khi gặp429, không tiếp tục lượt sau.

DB hiện có đúng hai mẫu trong leaf Sổ: một lò co19.500đ, một A4 55.500đ. Với yêu cầu không lò xo và dưới50 nghìn, không có quyền bịa mẫu A6/giấy80gsm/hàng vừa giá. Đây là quan sát tại leaf đang xét, **không chứng minh toàn cửa hàng không có đồ ghi chép phù hợp**; LLM cần tìm nhóm tương đương hoặc giải thích hướng khác một cách có ích.

## Nghiên cứu đối chiếu trong đợt này

- [Shopping Reasoning Bench, EMNLP 2026](https://aclanthology.org/2026.emnlp-main.1576.pdf): đọc phần phân loại suy luận và hướng dẫn viết rubric/kiểm định người chấm (§3, Appendix B). Áp dụng kiểm các tiêu chí nhỏ theo từng lượt và tách hướng dẫn mua sắm khỏi việc chốt SKU. Chưa tái lập benchmark hay kiểm định bằng chuyên gia bán lẻ.
- [Build, Judge, Optimize](https://arxiv.org/html/2603.03565v1): đọc §2–4 và hướng tối ưu toàn hành trình. Áp dụng phân lỗi giữa các bước, giữ feedback và chấm hội thoại sau thay đổi, thay vì xem mỗi node chạy đúng là xong. Chưa triển khai GEPA/MAMuT hoặc huấn luyện; không lấy kết quả paper làm kết quả dự án.
- [Schema Key Wording as an Instruction Channel](https://arxiv.org/html/2604.14862v1): đọc cách thí nghiệm và giới hạn. Bài dùng nhiệm vụ toán, không chứng minh tên field tự giải quyết tư vấn bán lẻ. Mô tả `basket`/`optional` và cờ mục tiêu là giả thuyết kỹ thuật phải kiểm thực tế; không gọi là tái lập kết quả paper.
- [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs): đối chiếu hỗ trợ schema của các model cấu hình. Inventory thật chỉ có Qwen27B, GPT-OSS120B/20B cho chat; không cấu hình model không có trong tài khoản. Trace có 429, cooldown và schema400, không quy tất cả lỗi cho thiếu key hoặc hiểu ý.
- [Philips Support về ấm đun nước](https://www.philips.co.in/c-f/XC000006139/can-i-heat-up-other-liquids-with-my-philips-kettle): đọc trang nguồn. Chỉ áp dụng giới hạn sử dụng của các mẫu bài viết đề cập; không coi đó là xác nhận mọi thiết bị của khách. Corpus nêu rõ không tự biến ấm đun nước thông thường thành nồi nấu.

## Kiểm tra phần mềm và phần còn thiếu

Baseline đầu đợt 2.561 passed/1 skipped; sau nhóm sửa hội thoại đầu 2.593 passed/1 skipped, artifact [JUnit](artifacts/backend_interactive_dialogue_final_20261106.xml), 274,59 giây. Skip là quyền symlink Windows; có warning Starlette/httpx. Frontend không đổi, giữ kết quả lịch sử121 passed/build thành công ở báo cáo trước, không ghi là chạy mới. Ruff scoped và diff-check qua. Các artifact lịch sử được giữ nguyên.

Kiểm cuối sau sửa scope: **2.640 backend passed, 1 skipped, 1 warning**, 288,71 giây, [JUnit](artifacts/backend_interactive_scope_verified_20261106.xml). Skip quyền symlink Windows; warning Starlette/httpx. Frontend sau sửa giữ draft: **123 passed/11 files**, [JUnit](artifacts/frontend_interactive_expired_text_20261106.xml); `vue-tsc` và production build qua, còn cảnh báo chunk model-viewer lớn. Test môi trường jsdom có cảnh báo thiếu WebGL/media; không phải đo loa/mic thật.

Hai lần chạy trung gian được giữ: `backend_interactive_constraints_final_20261106.xml` có2 lỗi nhầm taxonomy Dung dịch, và `backend_interactive_purchase_final_20261106.xml` có14 lỗi assertion do đổi thông báo validation (hành vi vẫn bác giá sai). Đã sửa nguyên nhân, không bỏ test hoặc đổi nhãn để coi chúng qua.

Vẫn cần kiểm reviewer trên cả câu đúng và sai đã đóng băng, dữ liệu hội thoại do nhân viên/người dùng chấm độc lập, dữ kiện nhãn đáng tin theo nhóm hàng và provider đủ ổn định. Mỗi sửa giảm một cơ chế lỗi, chưa chứng minh tư vấn tổng quát đạt mức nhân viên.
