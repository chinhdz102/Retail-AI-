# Sửa lỗi tư vấn và đánh giá thực tế — 15/11/2026

## Kết luận hiện tại

Đã sửa thêm lỗi tìm hàng, lượng mua, bằng chứng và xử lý câu trả lời. **Chưa nghiệm thu đạt mức nhân viên thật**: model dự phòng vẫn có câu lặp đề bài, thiếu lý do thực tế hoặc hỏi thừa; một số lượt bị chặn vì không sửa được nháp. Không dùng số test pass hoặc HTTP 200 làm điểm chất lượng tư vấn.

Đợt này tập trung backend tư vấn bằng text. Không thay model STT/VAD/TTS và không lấy kết quả text để kết luận chất lượng giọng nói. Giữ nguyên catalog cũ `data/winmart_products.json`, SHA256 `a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`.

## Nguyên nhân đã tái hiện và cách sửa

| Lỗi thật | Nguyên nhân | Thay đổi |
| --- | --- | --- |
| Khách muốn rau luộc nhưng chỉ thấy thịt | Planner dùng cách nấu làm truy vấn tên hàng; bước lọc tên loại bỏ rau tươi | Sửa truy vấn một lần trước soạn lời, giữ hãng/nhãn/quy cách/ngân sách; không thay tên loại hàng khách đã nêu bằng loại khác |
| Sửa nhầm truy vấn trong giỏ nhiều món | Model đánh số lại tập truy vấn thất bại từ 0, thay vì giữ index gốc | Schema giới hạn index; JSON fallback chỉ khôi phục index khi từ của truy vấn sửa khớp duy nhất một truy vấn gốc |
| “Bánh … vị thịt” quay lại danh sách thịt | Bước đa dạng hóa thêm hàng rẻ từ nhóm khớp thành phần/hương vị | Giữ tầng đúng loại hàng xuyên suốt bước lấy hàng giá rẻ; truy vấn nhóm chung lấy cả hàng thuộc nhóm dù tên không chứa danh từ chung |
| Mua 300g bị hiểu như gói 300g hoặc giá một kg | Chưa có trạng thái lượng mua riêng, model tự so giá/kg với tiền cả giỏ | Lưu `purchase_weights` có nguồn từ lời khách; áp dụng khi chỉ có một hàng cân tương ứng; cung cấp `price_basis` và `customer_purchase` tính sẵn |
| Bằng chứng sao chép sai làm hỏng lượt tư vấn | Bắt model chép nguyên văn nhãn, model thêm tên trường hoặc dấu không có trong nhãn | Model chọn `evidence_fields` có thật; code dùng nguồn gốc của SKU. Vẫn kiểm tra công dụng và review ngữ nghĩa; trường tồn tại không tự chứng minh mọi lời quảng cáo |
| Model tự viết mã hàng ngoài kết quả | JSON object bảo đảm cú pháp, không giới hạn miền ID | Composition dùng schema với enum ID ở các model hỗ trợ. Nếu provider trả `json_validate_failed`, thử JSON mode một lần; mọi kiểm tra cục bộ và review vẫn giữ |
| Có hàng nhưng trả “quá ngân sách”, hoặc không có hàng lại đoán giá | Reviewer chỉ biết hàng đã chọn, không thấy các lựa chọn còn lại; tiền ngân sách bị xem là giá hợp lệ | Reviewer nhận các candidate cùng giá lượng mua; chặn lời đoán giá dù trùng số ngân sách; tách tác vụ hướng dẫn khi không có hàng khớp |
| Hứa “đủ hai bữa” khi khách nói hai người | Nhầm số người với số bữa; review có lúc bỏ sót | Chặn mẫu cam kết khẩu phần thiếu cơ sở. Chỉ bỏ một mệnh đề khoe khẩu phần đứng riêng ở cuối câu nếu phần hướng dẫn hữu ích vẫn còn; sau đó vẫn review |
| Câu thành “có , phù hợp …” | Xóa giá lặp giữa câu làm mất ngữ pháp | Chỉ bỏ tiền/tên lặp ở ranh giới an toàn; trường hợp còn lại yêu cầu viết lại, không gửi câu bị cắt hỏng |
| Mua một dầu gội vẫn có dòng tổng giỏ | Composer tự đặt `selection_mode=basket` dù kế hoạch là recommend | Render theo decision mode đã xác định |
| Hỏi mua thịt lại chọn xương cùng quầy thịt | Nhóm hàng chung “Thịt” không phân biệt đầu sản phẩm xương | Không dùng hàng có đầu loại xương cho truy vấn thịt, vẫn cho phép khách chủ động tìm xương |
| Hỏi hướng dẫn lại mời chọn cỡ chai | Model chuyển nhiệm vụ guide thành câu hỏi bán hàng | Chặn câu hỏi chọn hãng/quy cách/mua thêm trong decision mode guide |

Phản hồi lỗi hàng cân vượt tiền giờ kèm phép tính và số gram tối đa của riêng món đó, nhắc vẫn phải dành tiền cho phần còn lại. Không tự đổi lượng khách đã chốt để ép vừa tiền.

## Nghiên cứu áp dụng

- [JSONSchemaBench (2025)](https://arxiv.org/abs/2501.10868): đánh giá riêng độ tuân thủ, phạm vi schema và chất lượng nội dung. Áp dụng bằng cách tách test cấu trúc với đọc lời tư vấn thật; không coi schema đúng là hiểu khách đúng.
- [The Hidden Cost of Structure (RANLP 2025)](https://aclanthology.org/2025.ranlp-1.124/): ràng buộc cấu trúc có thể làm giảm chất lượng sinh của một số instruction model; prompt thích ứng và ví dụ có ảnh hưởng. Đợt này rút gọn composer/reviewer, thêm ví dụ về cách viết lý do và chia riêng tác vụ không có hàng khớp. Đây là lựa chọn kỹ thuật cần kiểm chứng ở dự án, không phải bảo đảm hiệu quả từ paper.
- [Corrective Retrieval Augmented Generation](https://arxiv.org/abs/2401.15884): phát hiện kết quả lấy tài liệu không đủ tốt rồi sửa hướng tìm trước sinh câu trả lời. Áp dụng một phần ở truy vấn sản phẩm không có hit; không triển khai toàn bộ CRAG và không lấy giá/SKU ngoài cửa hàng qua web.
- [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs): tài liệu liệt kê strict mode cho Qwen 3.8 27B và GPT-OSS 120B/20B, nhưng vẫn nhấn mạnh schema không bảo đảm đúng ngữ nghĩa. Test thật vẫn gặp `json_validate_failed`; vì vậy có đường lùi định dạng có giới hạn và giữ kiểm tra sau sinh.

## Kiểm thử và bằng chứng

Lệnh hồi quy: `CATALOG_REPOSITORY=demo`, chạy 8 file test consultation, semantic review, Groq, retail API, catalog query, snapshot repository/API và measurements. **440 test pass trên workspace**. Bản commit xuất ra checkout độc lập có **449 test pass**; chênh 9 test là phần STT cũ được giữ trong commit, tách khỏi các sửa STT đang dang dở trong workspace. `git diff --check` sạch. Backend đã được khởi động lại với code mới và health trả `ok`.

Các artifact giữ nguyên cả thất bại, không lọc chọn câu đẹp:

| Artifact | Kết quả và cách đọc |
| --- | --- |
| `artifacts/advice_structural_before_20261115.json` | 3 lượt trước sửa: 1 trả được, 2 lỗi; trace cho thấy “rau luộc” làm mất rau và model tự dùng ID cũ không có trong candidates |
| `artifacts/advice_recovery_meal_r2_20261115.json` | Bản trung gian: 4/4 lượt trả được, giữ 300g và tính tổng đúng; câu còn quảng cáo dinh dưỡng, dùng rau mầm nhỏ và có cam kết khẩu phần, nên chưa đạt |
| `artifacts/advice_recovery_cross_category_final_20261115.json` | 8 lượt: 4 trả được, 4 lỗi; 1 câu trả được vẫn đoán giá trà nên đánh giá nội dung là không đạt. Có cả lỗi review/định dạng và hết hạn mức, không quy mọi lỗi cho quota |
| `artifacts/advice_no_match_final_20261115.json` | Câu không có trà đúng nhãn chuyển sang hướng dẫn pha trà không thêm đường, không hỏi lại hãng. Sau artifact này sửa thêm chặn nhầm cách nhắc lại giới hạn ngân sách |
| `artifacts/advice_recovery_http_final_20261115.json` | HTTP bản trước các sửa cuối: 2/7 lượt 200, 5/7 lỗi; bắt thêm câu dầu gội hỏng ngữ pháp do xóa giá và lời khuyên chỉ lặp ngân sách |
| `artifacts/advice_recovery_meal_shipping_20261115.json` | 4 lượt trước sửa index: 2 trả được, 2 bị chặn; trace bắt chính xác việc index truy vấn bị đánh lại từ 0 và một lượng 719g vượt tiền. Câu trả được vẫn có lý do chung chung |
| `artifacts/advice_recovery_http_verified_20261115.json` | Lần HTTP sau sửa index và phản hồi phép tính hàng cân; xem kết quả cuối bên dưới |

Các flow và thay đổi không đồng nhất giữa các vòng; không dùng tỷ lệ giữa hai vòng để suy ra cải thiện thống kê. Test lặp lại có thể ảnh hưởng quota. Bộ 35 câu gốc và long flow cũ đã có trong các báo cáo trước; đợt này dùng các ca thất bại và đổi ngành hàng để truy nguyên nhân, không tuyên bố chạy lại toàn bộ 35 câu trên phiên bản này.

## Việc chưa đạt

- Câu trả lời của model dự phòng chưa ổn định về sự tự nhiên và việc chủ động đưa lời khuyên cụ thể; reviewer cũng có false positive/false negative.
- Khi hết hạn mức, đổi key cùng tổ chức không tạo thêm quota. Pipeline cần nhiều lượt LLM nên tiêu tốn token đáng kể; rút gọn prompt giảm đầu vào nhưng chưa có thí nghiệm đối chứng đủ để kết luận giảm latency đầu cuối.
- Chưa có căn cứ nói mọi ngành hàng đạt mức nhân viên thật. Các câu không được kiểm tra hoặc bị chặn vẫn là chưa đạt, không tính thành công.

## Kết quả HTTP cuối và sửa sau khi đọc nội dung

`advice_recovery_http_verified_20261115.json` chạy **đủ 7 lượt**, `complete=true`: **4 HTTP 200, 3 HTTP 502**. Đọc thủ công **không nghiệm thu lượt nào là đạt đủ yêu cầu**: lượt đầu chọn xương heo rồi khuyên xào; lượt hai chỉ chọn thịt, thiếu rau; dầu gội chỉ có lời quảng cáo và hướng dẫn cuối hỏi thừa cỡ chai. Hai lỗi đầu không phải do quota: semantic reviewer đã chấp nhận nội dung chưa tốt.

Sau đó thêm regression và sửa phân biệt xương/thịt, chặn câu hỏi bán hàng trong guide. Artifact `advice_recovery_shipping_smoke_20261115.json` gọi thật thêm một câu về thịt nạc 300g và rau luộc: trả được trong 10,34 giây nhưng chỉ đưa hướng dẫn chung, không có lựa chọn SKU, nên vẫn không đạt yêu cầu chốt hai món. Không tính câu này thành công chất lượng. Chưa chạy lại toàn bộ 35 câu sau hai sửa cuối.

Điểm còn cần nghiên cứu tiếp là tính nhất quán của planner/composer/reviewer khi fallback xuống 20B, đặc biệt việc hoàn thành đủ các món được yêu cầu và chủ động chọn hàng có chứng cứ. Thêm guard chỉ chặn được câu sai; không thay thế việc nâng chất lượng suy luận/lựa chọn.
