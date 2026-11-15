# Kiểm tra tư vấn bằng text thật — 15/11/2026

**Cập nhật tiếp:** xem [báo cáo nghiên cứu, sửa truy xuất và kiểm tra voice](consultation_research_followup_20261115.md). Các số liệu dưới đây giữ theo lần chạy trước; không phải kết quả nghiệm thu mã hiện tại.

Đợt này kiểm tra các câu khách đã đưa bằng HTTP thật tới backend localhost, dùng catalog cũ. Các câu bị HTTP 502 hoặc bị guard chặn được giữ là **chưa đạt**, không tính thành công. API quota và lỗi nội dung được tách riêng.

## Đã sửa

- Câu so sánh như “hai cái này gần giống nhau, sao giá chênh” và “cùng mức giá thì loại nào dùng thích hơn” không còn đi vào nhánh hỏi giá đơn thuần. Khi chưa có tên hai sản phẩm, hệ thống hỏi đúng hai sản phẩm cần so sánh. Test live trả câu: “Bạn có thể cho mình biết cụ thể hai mặt hàng bạn đang so sánh là gì không?”
- Câu hỏi vị trí như “nước giặt ở đâu” và “khăn giấy nằm khu nào” đi qua lookup vị trí riêng. Nếu catalog không có vị trí đã xác minh, câu trả lời ngắn là chưa có vị trí chính xác và nhờ nhân viên tại quầy chỉ giúp; không hỏi số lượng và không tự đặt kệ.
- Tách biến thể nhãn khỏi loại hàng khi tìm kiếm. `trà không đường` được tìm như loại `trà` với điều kiện nhãn `không đường`, tránh lấy kẹo hoặc sản phẩm chỉ tình cờ chứa hai từ.
- Các câu hỏi tiếp nối có ngân sách như “thịt bò hoặc ba chỉ, khoảng 200 nghìn” giữ luồng tư vấn nếu đây là giới hạn mua hàng, thay vì bị coi là yêu cầu tra giá độc lập.
- Reviewer hiện yêu cầu bốn kiểm tra độc lập: hiểu nhu cầu, quyết định/làm rõ, căn cứ sản phẩm và ích lợi thực tế. Thiếu một mục hoặc có mục thất bại thì không phát hành câu trả lời.
- Reviewer có thể trả `repair_action=clarify`: vòng sửa chuyển sang hỏi một chi tiết còn thiếu thay vì tiếp tục cố chọn SKU. Đã kiểm chứng ở test scripted và trace Groq thật `consultation_repair_action_gift_20261115.json`; câu hỏi đã tiến được nhưng lựa chọn “trải nghiệm” vẫn rộng hơn hàng trong cửa hàng, nên chỉ chấm đạt một phần.
- Guard chặn các khẳng định không có nhãn chứng minh như “ăn ngay”, “có nhân béo”, “độ ngọt rất thấp”, “không làm ngứa miệng” và lời hứa một chai đủ phủ toàn bộ sàn. Các lời quảng cáo về no lâu vẫn cần reviewer đánh giá; không thêm một bộ lọc cứng cho mọi kiến thức ăn uống. Hướng dẫn chung như pha loãng tại nhà được giữ khi không gán cho SKU.
- Lỗi provider `json_validate_failed` hạ encoding có giới hạn: strict JSON schema → JSON object → JSON kiểm tra cục bộ. ID, nhãn, giá và review vẫn bắt buộc. Rate limit không bị coi là lỗi định dạng và không được retry vô hạn.
- Tách sở thích tương đối như “không quá ngọt”, “đỡ ngọt”, “đáng tiền” khỏi bộ lọc bắt buộc có nguyên văn trên nhãn. Biến thể cụ thể như “không đường” vẫn giữ bắt buộc.
- Bộ audit lưu `error_code=assistant_busy|assistant_unavailable`, giúp phân biệt hết lượt model/key với lỗi render/review.

## Kiểm tra

- 857 test backend pass sau các sửa cuối; nhóm consultation/catalog có 328 test pass. Có 23 test hồi quy mới trong `tests/test_consultation_customer_review.py`; các test có model giả kiểm tra luồng và điều kiện chặn, không chấm chất lượng LLM thật.
- So sánh giá không có sản phẩm: live trả câu hỏi nhận diện, không trả “không thấy trong catalog”.
- Quà tặng mẹ chưa rõ sở thích: live trả câu hỏi chọn nhóm quà thay vì tự chọn dao hoặc mỹ phẩm.
- Hỏi vị trí: hai câu mẫu trả HTTP 200 trong khoảng 30–70 ms, không gọi LLM.
- Baseline 56 lượt: 13 HTTP 200, 43 lỗi. Đọc nội dung thấy các lỗi thực tế được nêu phía dưới; không suy chất lượng từ trạng thái HTTP.
- Bộ 19 lượt live trung gian: 8 HTTP 200, 11 lỗi. Bản này chạy trước sửa ngân sách tiếp nối và bỏ lọc sở thích quá chặt; không gọi là điểm của mã cuối.
- Bộ `release` ghi 11 lượt: 3 HTTP 200, 7 HTTP 502 và 1 HTTP 404. Lượt 404 là do mình khởi động lại backend để nạp sửa tiếp theo làm mất session đang test; loại lượt đó khỏi đánh giá lỗi tư vấn. Không dùng bộ này để nghiệm thu mã cuối.

## Các lỗi thật phải giữ trong báo cáo

- Bánh bao được nói “ăn ngay, no lâu, có nhân béo” dù không có mô tả hỗ trợ.
- Aquafina 5L được nói có “độ ngọt rất thấp, không làm ngứa miệng”; reviewer cũ đã chấp nhận.
- Khách mua quà cho mẹ bị tự chọn dao nhà bếp; câu này không đủ cơ sở để coi là tư vấn tốt.
- Luồng bữa tối có lúc chọn 1kg gà với 100g rau mầm, lý do chỉ lặp tiện lợi/khẩu phần. Reviewer bốn mục vẫn có false negative.
- Luồng da nhạy cảm có reviewer tự hỏi tuổi của “bé” dù khách không nói mua cho bé; vì vậy không thể coi reviewer là nguồn sự thật.
- Thử tăng reasoning của GPT-OSS-20B lên medium chưa giải quyết ổn định: có câu trả lời so sánh chung chung khi chưa biết hai sản phẩm. Không đổi cấu hình production sang medium dựa trên mẫu này.
- Bản `release` đã tìm được trà Oolong không đường nhưng chọn lốc 6 chai thay vì chai lẻ; câu ăn sáng vẫn quảng cáo “giàu chất dinh dưỡng”. Sau đó đã mở rộng guard bắt cả cụm “giàu chất dinh dưỡng”, chưa coi bộ release là đạt.

## Vấn đề còn lại

- Fallback GPT-OSS-20B vẫn có lúc chọn quy cách quá lớn, chọn rau chưa phù hợp hoặc viết lý do chung chung. Reviewer mới chặn được một phần; chưa thể tuyên bố tư vấn đạt mức nhân viên ở mọi ngành hàng.
- Một số lượt bị 502 vì rate limit; một số lượt khác bị chặn bởi guard semantic. Đây là hai loại lỗi khác nhau và cần được theo dõi riêng.
- Luồng nhiều lượt cần chạy lại khi quota hồi phục để đánh giá giữ ngân sách, món đã có, loại hàng bị loại và quy cách. Không được suy ra chất lượng từ các lượt 502.

Catalog cũ giữ SHA-256 `a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`.
Không thay provider STT/VAD/TTS trong đợt tập trung tư vấn này.

Artifact chính: `consultation_user_review_before_20261115.json`,
`consultation_user_review_after_20261115.json`, `consultation_user_review_final_20261115.json`
và `consultation_user_review_release_20261115.json` trong thư mục `artifacts/`.
Mã cuối kiểm tra riêng trong `consultation_user_review_shipping_20261115.json`.
Trace chẩn đoán có planner/composer/reviewer: `consultation_drinks_checked_20261115.json`,
`consultation_compare_checked_20261115.json`, `consultation_checked_gift_final_20261115.json`.
Hai trace tên `consultation_child_clarification` và `consultation_gift_clarification` ghi trong
lúc thử nghiệm cách hỏi theo mẫu sau đó đã bỏ; không dùng chúng nghiệm thu mã cuối.

## Cơ sở phương pháp

- [FLASK (ICLR 2024)](https://arxiv.org/abs/2307.10928): chấm theo kỹ năng nhỏ thay vì một điểm tổng; áp dụng thành bốn kiểm tra intent/decision/grounding/usefulness.
- [G-Eval](https://arxiv.org/abs/2303.16634): dùng rubric có cấu trúc cho đánh giá sinh ngôn ngữ; áp dụng bằng JSON checks và reject khi thiếu check.
- [Corrective Retrieval Augmented Generation](https://arxiv.org/abs/2401.15884): kiểm tra kết quả truy xuất và sửa query trước khi sinh; áp dụng cho search thiếu hit, vẫn giữ nguyên ràng buộc loại hàng/nhãn.
- [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs): dùng strict schema khi model hỗ trợ, có đường lùi giới hạn và vẫn validate/render/review sau sinh.
