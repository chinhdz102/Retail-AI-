# Cơ sở nghiên cứu cho tư vấn mua sắm hội thoại

Bản này ghi thiết kế ở đợt đầu. Các thay đổi tiếp theo về projection bằng chứng,
review bản nháp và voice nằm ở [nghiên cứu bổ sung](consultation_voice_research_20261101.md)
và [báo cáo đo thực tế](consultation_voice_audit_20261101.md); giới hạn 36 ứng viên dưới
đây là thông số lịch sử, phiên bản thử nghiệm mới dùng tối đa 48.

Ngày: 01/11/2026. Phạm vi: sửa khả năng hiểu nhu cầu, hỏi tiếp, chọn hàng và diễn đạt cho trợ lý bán lẻ tiếng Việt. Đây là ghi chú thiết kế đã đọc đối chiếu với mã nguồn, không phải bằng chứng người dùng đã đánh giá hệ thống đạt.

## Những nguồn đã đọc và cách áp dụng

### ProductAgent / PROCLARE — EMNLP Industry 2025

- Bản gốc: <https://aclanthology.org/2025.emnlp-industry.25/>
- Toàn văn: <https://aclanthology.org/2025.emnlp-industry.25.pdf>
- Đọc phần 3, 4.2–4.3, 5: bài toán nhu cầu chưa rõ, vòng lặp phân tích nhóm hàng → truy xuất → hỏi làm rõ, bộ nhớ có cấu trúc, đánh giá hội thoại mô phỏng.
- Điểm áp dụng: ghi nhu cầu và câu hỏi còn thiếu trong session; đối chiếu loại hàng với taxonomy thật trước khi tìm; đưa quan sát sản phẩm về cho bước viết câu trả lời. Không để mô hình chỉ gọi tool rồi kết thúc trước khi đọc kết quả.
- Khác biệt có chủ ý: paper đưa ba câu hỏi lựa chọn trong mỗi lượt. Demo cửa hàng này giới hạn một câu hỏi tiếp để giảm cảm giác điền biểu mẫu. Đây là quyết định thiết kế cho ứng dụng, không phải kết luận thực nghiệm của paper.
- Giới hạn: PROCLARE dùng người dùng mô phỏng biết trước món đích và 2.000 phiên sinh tự động. Kết quả truy xuất của paper không chứng minh cách nói tiếng Việt trong ứng dụng giống nhân viên thật.

### PEPPER — Findings of EMNLP 2025

- Bản gốc: <https://aclanthology.org/2025.findings-emnlp.1067/>
- Toàn văn: <https://aclanthology.org/2025.findings-emnlp.1067.pdf>
- Đọc phần vấn đề target-biased simulation, thiết kế target-free, Preference Coverage và phần 5.1 đối chiếu người dùng.
- Điểm áp dụng: 35 câu người dùng gửi là tập phát triển đã lộ, không gọi là held-out. Không chấm tốt chỉ vì chọn đúng một SKU được cài sẵn. Kiểm tra việc hỏi thiếu gì, giữ sở thích, phản ứng với thay đổi và không lặp món bị từ chối.
- Tách các câu hỏi đứng riêng khỏi câu hỏi có ngữ cảnh: “cái này đắt” ở phiên mới cần hỏi món gốc; sau khi khách đã xem món thì phải giải quyết tham chiếu đó.
- Đánh giá lời nói theo các chiều riêng: chủ động giúp khách tiến bước, mạch hội thoại, bám sở thích. Câu ngắn/lịch sự hay HTTP 200 không đủ làm thước đo.
- Giới hạn: paper đánh giá trên miền phim/sách và dùng cả LLM đánh giá, không xác nhận độ phù hợp của tư vấn thực phẩm, mỹ phẩm hoặc hóa chất. Không sao chép điểm số của paper thành điểm hệ thống này.

### Jannach và cộng sự — A Survey on Conversational Recommender Systems

- Bản gốc/toàn văn: <https://arxiv.org/abs/2004.00646>, <https://arxiv.org/pdf/2004.00646>
- Đọc phần về ý định hội thoại và phần 5.1 Request, Recommend, Explain, Respond (bản PDF trang 17–20).
- Điểm áp dụng: trợ lý có nhiều việc khác nhau: hỏi nhu cầu, giới thiệu, so sánh, giải thích và đáp lại chỉnh sửa. Không quy mọi câu thành tìm tên hàng.
- Lọc ràng buộc và xếp hạng là hai bước riêng. Giảm giá, đổi quy cách hoặc đổi hãng phải giữ ràng buộc khách còn muốn; không nới điều kiện âm thầm khi không có kết quả.
- Lời giải thích nên nối sở thích của khách với đặc tính thực sự có. Giá/quy cách/nhóm hàng có thể giải thích được; “tốt hơn”, “ngon hơn”, “an toàn” cần nguồn khác.
- Giới hạn: survey mô tả nhiều hướng và đánh đổi; không có một prompt hoặc mô hình nào được chứng minh giải quyết toàn bộ các ca bán lẻ này.

### Nhãn hàng và dữ liệu sản phẩm

- Codex CXS 1-1985: <https://www.fao.org/4/Y2770E/y2770e02.htm>
- GS1 Global Data Model: <https://www.gs1.org/standards/gs1-global-data-model-attribute-implementation-guide/13-1>
- Đối chiếu tài liệu nghiệp vụ hiện có tại `docs/product-consultation-grounding.md`.
- Công dụng/hướng dẫn/dinh dưỡng không tự sinh từ tên hàng. “Ít đường/không đường” trong tên là bằng chứng về cách sản phẩm được ghi tên, chưa phải bảng số gram đường, vị ngọt cảm nhận hay sự phù hợp về y tế.
- Tồn kho nhập từ snapshot và kệ gợi ý cho demo phải được phân biệt với thông tin tại cửa hàng thật.

## Quyết định triển khai

1. **Chọn hành động trước khi tìm hàng.** `delegate`, `clarify`, `recommend`, `compare`, `locate` là hành động tổng quát, không có bảng 35 câu → 35 đáp án.
2. **Bộ nhớ hội thoại.** Lưu nhu cầu đã hiểu, câu hỏi đang chờ và ràng buộc trong `ShoppingSessionState.consultation`. ID của hàng đang hiển thị là nguồn cho tham chiếu, không dùng lịch sử văn bản trợ lý làm chỉ dẫn.
3. **Nhóm hàng có thật.** Mô hình được thấy các facet của catalog; bộ giải nghĩa xác nhận nhóm trước khi tìm. Nhóm không xác định không được chuyển thành tìm toàn cửa hàng.
4. **Bộ lọc ở máy chủ.** Giá mỗi món/tổng, điều kiện dưới giá, quy cách, từ loại trừ và dấu hiệu ít/không đường được lọc riêng với bước viết lời tư vấn.
5. **Viết sau khi có quan sát.** Mô hình nhận tên, giá, nhóm, hãng và đơn vị có cấu trúc. Không đưa HTML/mô tả tự do của catalog vào lời chỉ dẫn.
6. **Giá/tổng được tính tại ứng dụng.** Placeholder liên kết với candidate đã chọn. Số lượng là số gói bán lẻ, không tự đổi thành khẩu phần hoặc tuyên bố đủ bữa.
7. **Kệ do ứng dụng trả.** Không cho bước viết văn tự bịa “rẽ trái”, “gần cửa”, “lối A”. Kệ planogram phải mang nhãn demo.
8. **Thử sửa đầu ra sai.** Một lần viết lại khi sai liên kết/giá hoặc có nhóm khẳng định thiếu bằng chứng. Nếu lựa chọn vẫn hợp lệ thì có thể trả tên/giá đã kiểm tra; lỗi dịch vụ không được dẫn tới chọn hàng ngẫu nhiên.
9. **Lọc trước khi chốt top-k.** Đọc các trang trong nhóm đã xác định, có giới hạn 200 dòng/nhu cầu, trước khi áp ràng buộc nhãn/quy cách. Tập cuối đưa sang mô hình tối đa 36 ứng viên. Không xem “20 dòng đầu chưa khớp” là “catalog không có”. Đây là quyết định kỹ thuật từ lỗi đo được trong ứng dụng, không phải con số được các paper khuyến nghị.

## Điều nghiên cứu chưa giải quyết thay cho dữ liệu và kiểm thử

- Prompt và các guard hữu hạn không bảo đảm loại bỏ mọi khẳng định sai hoặc mọi cách diễn đạt thiếu tự nhiên.
- Chưa có dữ liệu chuẩn về độ ngọt cảm nhận, da nhạy cảm, an toàn quanh thú cưng, độ lưu mùi, hiệu quả giặt, số lần dùng hoặc hạn dùng của phần lớn SKU.
- Muốn xác nhận “giống nhân viên thật” cần người Việt dùng thử, chấm các hội thoại nhiều lượt và so sánh với phiên bản trước. Kiểm thử code và tự đọc của agent chỉ là bước sàng lọc.
- Với 3.081 sản phẩm, chưa có bằng chứng cần thêm vector database để giải quyết các lỗi hội thoại này. Thay đổi hiện tại tập trung vào chính sách hội thoại và luồng dữ kiện.

Các bản PDF đã được tải, trích toàn văn để đọc và lưu SHA-256 trong `artifacts/consultation_research_manifest_20261101.json`. Báo cáo kết quả thực tế nằm riêng để không trộn khuyến nghị nghiên cứu với kết quả đã chạy.
