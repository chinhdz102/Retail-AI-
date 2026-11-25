# Kiểm tra hội thoại thực tế và sửa tư vấn — vòng 2, 06/11/2026

**Kết luận: chưa đạt mức nhân viên bán hàng.** Đã trò chuyện trên giao diện và chạy hội thoại nhiều lượt qua API thật, đọc từng câu để sửa cơ chế lỗi. Bản cuối của bộ 10 lượt có 8 lượt không tư vấn được và 2 lượt có nội dung nhưng không đạt rubric. Hai lượt bổ sung trên giao diện cũng không tư vấn được. Không lấy số test phần mềm qua hoặc HTTP200 làm điểm chất lượng tư vấn.

Tiếp nối [vòng hội thoại trước](interactive_employee_dialogue_20261106.md). Không thay câu trả lời bằng mẫu riêng cho lẩu, nước uống hay một mã hàng. Giữ nguyên các capture lỗi để đối chiếu.

## 1. Đã trò chuyện và chấm thế nào

[Bộ câu hỏi khai báo trước](../../evaluation/cases/interactive_round2_20261106.json) gồm 5 hành trình, mỗi hành trình 2 lượt. Khách đổi điều kiện ở lượt sau: thịt nhúng lẩu rồi bỏ thịt heo; một chai nước mang đi làm rồi muốn nhỏ hơn; dầu gội đáng tiền rồi giảm tiền mua ban đầu; nước rửa chén và túi rác rồi chỉ mua nước rửa chén; da nhạy cảm rồi hỏi điều trị mụn.

Đây là tập phát triển do Codex viết và tự chấm sau khi đọc nội dung, **chưa được nhân viên/người dùng chấm độc lập, chưa phải tập ẩn**. Các trường hợp không chạy được và fallback vẫn ở trong mẫu số. Không có đáp án SKU cố định trong production.

| Capture | Lượt trong mẫu số | Đạt | Yếu | Sai | Không tư vấn được |
|---|---:|---:|---:|---:|---:|
| HTTP v1 | 10 | 0 | 0 | 1 | 9 |
| HTTP v2, sau restart nguồn mới | 10 | 0 | 0 | 2 | 8 |
| Giao diện, bữa sáng và đổi sở thích | 2 | 0 | 0 | 0 | 2 |

Hai lần HTTP dùng lại cùng bộ phát triển; không coi là 20 câu mới độc lập, không suy ra tỷ lệ chính xác của toàn hệ thống. Các thay đổi giữa v1 và v2 cùng điều kiện provider khác nhau cũng không cho phép kết luận thống kê rằng chất lượng đã tăng.

- V1: [raw](artifacts/interactive_round2_http_20261106_v1.json), [chấm riêng](artifacts/interactive_round2_http_20261106_v1_manual.json).
- V2: [raw](artifacts/interactive_round2_http_20261106_v2.json), [chấm riêng](artifacts/interactive_round2_http_20261106_v2_manual.json). Hash nguồn trên đĩa không đổi trong từng capture. Backend v2 được restart với PID15408 trước khi chạy; không suy phiên bản process chỉ từ hash trên đĩa.
- Giao diện: [ghi nhận hai lượt](artifacts/interactive_round2_ui_breakfast_20261106.json), [ảnh màn hình](artifacts/interactive_round2_ui_breakfast_20261106.jpg). Nhập câu và đọc phản hồi bằng CUA trên `http://127.0.0.1:5173/`. Chưa bật micro và không phát âm thanh giả vào micro.

Lượt gửi đầu tiên trên giao diện gặp phiên hết hạn. Ô nhập giữ nguyên cả câu để gửi lại; sau khi gửi lại, câu hỏi xuất hiện đúng trong hội thoại nhưng câu tư vấn vẫn fallback. Đây là hai vấn đề riêng.

## 2. Lỗi thật tìm thấy

**Hiểu sai ý ngay trước LLM.** Câu có “cả hai… đồ cần mua” bị regex bỏ dấu đọc thành so sánh “hai… đó”. Trace chỉ mất0,087 giây và không gọi LLM, nhưng lại hỏi khách tên hai món cần so sánh. Đây là lỗi router, không phải model không biết tư vấn. [Trace](artifacts/interactive_round2_compact_none_trace_20261106.json).

**Chọn quá nhiều và tự bịa lý do.** Lượt nước uống từng đưa hai chai1,5L khi khách nhờ chọn một, gọi một chai là “gọn nhất”, tự nói nhựa/nhẹ/vừa balo trong khi có lựa chọn450ml và không có dữ kiện kích thước ngoài/vật liệu. Reviewer vẫn cho qua. [Trace trước sửa](artifacts/interactive_round2_trace_before_20261106.json).

**Chọn sai dạng mua.** Trace khác chọn gói đồ uống125g khi khách cần một chai, rồi suy không có ga/không cà phê từ tên không nhắc thành phần. Câu hướng dẫn trong prompt và review không đủ để đảm bảo model suy luận đúng. [Trace](artifacts/interactive_round2_choice_120b_none_20261106.json).

**Giỏ thiếu món trở thành lỗi toàn bộ.** Trong3081 tên hàng của snapshot đang chạy không thấy túi rác phù hợp với từ khóa tên đã kiểm. Planner ép nhu cầu đó vào nhóm dụng cụ cọ rửa; selector bỏ món sai, nhưng renderer bác cả phần nước rửa chén đúng. Điều này không chứng minh toàn cửa hàng không có túi rác. Câu hỏi còn được sinh lặp ở nhiều field với cách viết khác nhau. [Trace](artifacts/interactive_round2_compact_household_trace_20261106.json).

**Phủ định nuốt sang câu sau.** “không cà phê. Chọn giúp một chai thôi” bị giữ làm một điều kiện loại trừ dài. Scope theo nhóm cà phê vì thế vẫn được ưu tiên; planner không được thấy đủ nhóm nước khoáng.

**Chặn nhầm so giá hợp lệ.** Một SKU14000đ được chọn rồi nói “giá dưới20 nghìn” đúng ngân sách khách nhưng bị coi là giá bịa. Không nên bắt model bỏ mọi lý do có số khi phép so sánh có thể kiểm bằng dữ kiện thật.

**Chất lượng nội dung vẫn chưa đạt sau sửa.** HTTP v2 còn chọn thịt bò xay và mở đầu “Nếu bạn muốn tự thái và nhanh chóng”, rồi giải thích không cần thái thêm. Khách chấp nhận tự thái, không yêu cầu tốc độ; câu chưa giúp chọn loại thịt phù hợp để nhúng. Lượt nước450ml giữ được một món và đúng giá5500đ nhưng chủ yếu đọc tên/giá, còn khẳng định không có ga chưa đủ căn cứ trong projection được cấp. Cả hai đều không đạt rubric.

**Lời chăm sóc da bị đổi nghĩa.** V1 có câu “không chứa thành phần làm se lỗ chân lông” và gắn non-comedogenic với giảm kích ứng. Nguồn hướng dẫn có các khái niệm làm se/kích ứng và tránh tắc lỗ chân lông, nhưng không hỗ trợ phép ghép nghĩa đó. Có câu tham khảo bác sĩ không làm toàn câu tự trở thành đạt.

**Dịch vụ là một phần lỗi lớn, nhưng không phải toàn bộ.** Log v2 có `structured-validation`,429, cooldown và schema400. Hai lượt UI cuối có log tương ứng `provider-rate-limit`; trước đó cũng có schema failure. Một lời chào Qwen thành công không chứng minh chuỗi tư vấn đủ ngân sách token để chạy. Không đủ bằng chứng quy mọi lỗi cho quota hoặc khẳng định hết quota theo ngày.

[Log chẩn đoán đã giới hạn field](artifacts/interactive_round2_server_diagnostics_20261106.log) giữ54 dòng model/slot/loại lỗi, lý do và bộ đếm trước restart cuối; không chép raw exception hay credential. Backend nguồn cuối đã restart thành PID1252, `/health` trả200. Đây chỉ là xác nhận phục vụ nguồn mới, không phải một lần hội thoại tư vấn đạt.

## 3. Đã sửa cơ chế chung

| Thay đổi | Mục đích và giới hạn |
|---|---|
| Nhận cụm so sánh theo danh từ và dấu tiếng Việt; có nhánh riêng cho câu ASCII | Không để “hai…đồ” thành “hai…đó”. Test cả văn phòng phẩm, đồ gia dụng và cắm trại. Không mã hóa câu trả lời theo ngành hàng. |
| `max_options` trong trạng thái quyết định và schema động | Khi khách nhờ chọn một, schema chỉ nhận một lựa chọn; giữ qua lượt tiếp và bước tìm lại. Số lựa chọn khác số đơn vị mua. |
| Gửi các lựa chọn đã thấy cho reviewer khi câu có so sánh | Reviewer được đối chiếu giá, quy cách và measurement thật khi nghe “nhỏ/rẻ nhất”. Đây vẫn là LLM review, chưa bảo đảm hiểu đúng. |
| `unresolved_needs` và `unfulfilled_searches` | Giữ phần nhu cầu chưa tìm được nhóm hoặc chưa có ứng viên phù hợp. Phần giỏ đúng vẫn có thể trả lời nếu nói rõ phần thiếu; không ép phụ kiện vào vai trò sai. |
| Giá tạm tính cho giỏ chưa đủ | Cả native text binding và nhánh typed fallback đều không gọi tiền một phần là đủ giá toàn giỏ. Nhánh typed fallback còn nêu phần cần chọn thêm. |
| Câu hỏi chỉ sinh một lần trong `pending_question.question` ở native wire | App tự đồng bộ trường local và ghép câu hỏi; giảm lỗi do model viết cùng một câu hỏi ở ba chỗ. |
| Giữ khả năng sửa đáp án đã trả lời | Chỉ rút schema clarification khi không có câu đang chờ **và** không có slot đã trả lời. Không mất quote/ID cần thiết để khách sửa ngân sách ở lượt sau. |
| Ngắt điều kiện phủ định tại dấu chấm hết câu | Giữ đúng “không cà phê”; không cắt số thập phân như1.5L. |
| So ngân sách bằng phép tính có giới hạn | Chỉ cho phép so giá của một SKU đã bind với trần tiền thực của khách và comparator đúng. Không hợp thức hóa giá thị trường hoặc phép so sai. |
| Thu gọn prompt và context review | Giữ dữ kiện nguyên văn một lần theo ID nguồn; bỏ lặp customer/products. Không bỏ bước kiểm nội dung để giả tạo tốc độ. Chưa có ablation cùng điều kiện để công bố mức tiết kiệm token hay latency. |
| Tách suy luận mua sắm và thuộc tính SKU trong chỉ dẫn | Cho phép kiến thức thông thường về chức năng, phối hợp và chuẩn bị; công dụng y tế, an toàn, vật liệu, thành phần, khẩu phần vẫn cần căn cứ. Không coi “thiếu thuộc tính DB” là lý do cấm mọi lời tư vấn. |

Nguồn chính: `customer_consultation.py`, `consultation_policy.py`, `consultation_text.py`, `consultation_verification.py`, `query_understanding.py`. Các bổ sung kiểm tra nằm trong `test_consultation_choice_scope.py`, `test_consultation_partial_basket.py` và test phủ định. Fake-model regression kiểm hợp đồng phần mềm, **không chứng minh model thực chọn đúng**.

Sửa cuối về nhãn giá/nhu cầu thiếu của typed fallback diễn ra sau capture v2 và UI. Nó có scoped regression; chưa có hội thoại provider sau sửa đó. Không gán ngược kết quả capture cho nguồn cuối cùng.

## 4. STT/VAD/TTS tiếng Việt

[Replay cuối](artifacts/interactive_round2_vietnamese_audio_20261106_v2.json) chạy14 request:3 clip giọng người,6 câu retail bằng Azure TTS và5 clip không lời ESC-50. Cả14 trả HTTP200; điều này không đồng nghĩa cả14 nhận đúng.

| Nhóm | Kết quả quan sát |
|---|---|
| FLEURS Việt, một clip | WER0 trên quy tắc chuẩn hóa hiện có. |
| ViVoice Nghệ An | WER0,3268; sai nhiều lời dù Whisper có confidence khá cao. Có cờ cần xác nhận. |
| ViMD, một clip | “rong riềng” thành “dòng diệt”, WER0,3333. Có cờ cần xác nhận. |
| Câu retail tổng hợp | Câu lẩu và nhờ tư vấn đúng; số1lít/100ml và150nghìn đúng nghĩa nhưng khác cách viết làm tăng WER; “Ariel” thành “A-RIN”, “mùi nồng” thành “mùi nông”. Hai lỗi sau có cờ cần xác nhận. |
| Năm clip không lời | Chó, gõ cửa, bàn phím, máy hút bụi và đồng hồ báo thức đều trả text rỗng, `speech_detected=false`, bị chặn trước Whisper. |
| TTS | Azure `vi-VN-HoaiMyNeural`, HTTP200,30528byte MP3,1,299giây. Chưa có chấm nghe độc lập, chưa kiểm loa/echo. |

STT primary trong phản hồi thật là **`whisper-large-v3`**; không đổi model chỉ để có tên mới. Zipformer Việt local làm verifier phụ. Đợt này sửa điều kiện bất đồng: chênh lệch nội dung đủ lớn không còn được bỏ qua chỉ vì Whisper tự tin; chuẩn hóa cụm số/đơn vị tương đương trước khi so. [Replay offline24 cặp đã lộ](artifacts/interactive_round2_confirmation_offline_20261106.json) đánh dấu6 cặp cần xác nhận. Đây là kiểm bất định, **không phải giảm WER hay tự sửa transcript**.

Không kiểm micro vật lý, quạt/điều hòa trong phòng người dùng, browserVAD từ luồng capture thật, ngắt lời, loa phát lại hoặc LiveKit end-to-end trong vòng này. Năm noise control không thay cho những kiểm tra đó. Artifact audio v1 được giữ với `complete=false`: harness lỗi đường dẫn sau khi ghi MP3, không tính là lần hoàn thành.

## 5. Nghiên cứu đã đối chiếu

- [ChatCRS, NAACL2025](https://aclanthology.org/2025.findings-naacl.17.pdf): đọc hướng tách lập mục tiêu, tri thức và hội thoại. Áp dụng tách nhu cầu/role khỏi dữ kiện SKU; chưa tái lập hệ thống hoặc benchmark của paper.
- [Clarify When Necessary, NAACL2025](https://aclanthology.org/2025.findings-naacl.306.pdf): đọc chi phí/lợi ích của hỏi làm rõ. Hướng áp dụng là chọn sơ bộ khi đủ ý, chỉ hỏi điều thật sự đổi quyết định. Chưa triển khai thuật toán INTENT-SIM.
- [ProductAgent, EMNLP2025](https://aclanthology.org/2025.emnlp-industry.25.pdf): đối chiếu bộ nhớ có cấu trúc, tool loop và candidate context. Áp dụng giữ slot/role/nhu cầu chưa đáp ứng; chưa thêm dense index hoặc tuyên bố tái lập hybrid retrieval của tác giả.
- [Shopping Reasoning Bench, EMNLP2026](https://aclanthology.org/2026.emnlp-main.1576.pdf): dùng hướng rubric theo mục tiêu và thay đổi sở thích ở từng lượt; không dùng điểm paper làm điểm của dự án.
- [Vietnamese ASR: A Revisit, EACL2026](https://aclanthology.org/2026.findings-eacl.345.pdf): đối chiếu đa dạng lời nói tự nhiên và chuẩn hóa lời nói/chữ/số. Replay giọng vùng miền và tách lỗi định dạng số khỏi lỗi ý nghĩa; không coi threshold bất đồng là tái lập cải tiến ASR của paper.
- [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs): schema đúng chỉ bảo đảm cấu trúc, không chứng minh chọn đúng loại hàng hoặc quan hệ bằng chứng đúng.

Không có căn cứ gọi một kỹ thuật/model là “mạnh nhất” cho dữ liệu này khi chưa so sánh cùng tập ghi âm và hội thoại. Nguồn tìm được nhưng không mở đọc được không được tính là đã đọc/áp dụng.

## 6. Kiểm tra phần mềm

- Toàn backend trước nhánh sửa subtotal cuối: **2682 passed,1 skipped,1 warning**,289,54giây. [JUnit](artifacts/backend_interactive_round2_delivery_20261106.xml). Skip quyền symlink Windows; warning Starlette/httpx.
- Sau sửa subtotal cuối: **272 passed**,1warning,4,11giây, tập liên quan partial basket/choice/customer/native wire/bound text. [JUnit](artifacts/interactive_round2_partial_final_20261106.xml). Không gọi272 là toàn bộ test và không cộng vào2682 để công bố full-suite mới.
- Ruff scoped cuối qua. Lần đầu sau sửa subtotal có một lỗi line length, đã chỉnh format và chạy lại qua.
- Frontend không sửa trong vòng này; không ghi test/build lịch sử thành kết quả chạy mới.
- [Manifest nguồn cuối](artifacts/interactive_round2_delivery_manifest_20261106.json) ghi hash và ranh giới kiểm thử. Các artifact trung gian lỗi được giữ nguyên.

## 7. Phần chưa đạt và thứ tự xử lý tiếp

1. **Ổn định khả năng gọi LLM trên chuỗi thật.** Hiện các slot/model cấu hình gặp429/cooldown và có schema400. Giữ giới hạn request, tránh chạy lại liên tục để “lọc” mất lượt lỗi. Cần biết rõ ngân sách token theo model và slot; thêm key không tự chứng minh tăng quota. Chưa có bằng chứng để giải bằng đổi prompt đơn thuần.
2. **Kiểm reviewer trên cả mẫu đúng lẫn mẫu sai đã đóng băng.** Các lời bịa vật liệu, không thành phần, kích thước/balo và quan hệ “tự thái/thịt xay” đã có capture. Cần đối chứng lời chuẩn bị thông thường đúng để tránh biến mọi lời tư vấn thành bị cấm. Reviewer có thể fallback cùng GPT-OSS20B với selector, vì vậy không gọi là độc lập về model hoặc thay thế đánh giá người thật.
3. **Đánh giá mục đích và dạng hàng trước khi trình bày.** Loại/tên đúng keyword chưa đủ; phải xét cách khách định dùng và quy cách một chai/gói/lốc. Tìm lại có phạm vi khi không phù hợp, giữ phần nhu cầu chưa đáp ứng; không thêm luật “gặp lẩu thì SKU X”. Hiện mới có quan sát dạng literal và LLM suy luận, chưa có bộ xếp hạng ngữ nghĩa đã kiểm định.
4. **Bổ sung dữ liệu nhãn có nguồn theo nhóm ưu tiên và hội thoại người thật chấm.** Phân biệt điều model có thể suy luận từ kiến thức mua sắm thông thường với thuộc tính chỉ nhà sản xuất/nhãn mới xác nhận. Mở rộng dữ liệu không có nghĩa viết sẵn đáp án.
5. **Đo tiếng Việt trên capture thực.** Tiếp tục so model bằng cùng audio, giữ phủ định/số/brand trong tiêu chí; sau đó kiểm micro, tiếng quạt, echo, VAD và ngắt lời toàn luồng. Cờ xác nhận giúp tránh tự mua sai, chưa làm nhận diện giọng địa phương trở nên chính xác.

Các mục còn mở này là công việc chưa hoàn thành, không được đổi thành “đã như nhân viên” chỉ vì regression qua. Demo vẫn phục vụ thử nghiệm có kiểm soát; chưa sẵn sàng cho khách thật.
