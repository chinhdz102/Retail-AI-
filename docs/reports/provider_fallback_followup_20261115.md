# Fallback key/model follow-up — 15/11/2026

## Kết quả

Đã kiểm tra cấu hình mà không in giá trị khóa: có **5 key khác nhau** ở các slot 0–4; model chính là `qwen/qwen3.8-27b`, fallback là `openai/gpt-oss-120b` rồi `openai/gpt-oss-20b`.

Đợt sửa này:

- truyền cả hai model dự phòng vào mọi request planner/composer/reviewer;
- thử toàn bộ key của model hiện tại trước khi chuyển model;
- giữ cooldown theo cặp `(model, credential_slot)` khi gặp 429;
- xoay vòng key khỏe sau mỗi lần thành công để không dồn tải vào slot 0;
- 401 khóa đúng key trong lượt hiện tại;
- 403/404 vẫn thử key khác vì có thể là quyền/model khác nhau giữa key;
- 5xx/network thử key kế tiếp thay vì loại bỏ cả model;
- 400/413/422 không lặp lại cùng payload trên mọi key;
- đọc `Retry-After` dạng giây, `1m12.5s`, `3s`, `ms` và reset token.

## Kiểm thử

- **391 test pass** với tư vấn, provider fallback, API, snapshot và quy cách.
- Snapshot commit tách riêng khỏi các sửa STT chưa commit: **400 test pass**.
- Test network có kiểm soát cooldown thật: ép 4 slot đầu của cả 3 model vào cooldown; request thực tế đi qua `openai/gpt-oss-20b` ở slot 4 và nhận HTTP 200. Artifact: `artifacts/fallback_routing_checked_20261115.json`.
- Một flow dầu gội thật trả được hai lượt HTTP 200 bằng Qwen; lượt hướng dẫn cuối bị 502 do semantic review, không phải lỗi không thử key/model. Artifact: `artifacts/advice_fallback_live_20261115.json`.

## Giới hạn

Đổi key không chắc vượt được hạn mức nếu các key thuộc cùng một tổ chức Groq. Tài liệu Groq nói rate limit áp dụng ở cấp organization và có thể chạm RPM/RPD/TPM/TPD/ITPM/OTPM; xem [Groq Rate Limits](https://console.groq.com/docs/rate-limits). Vì vậy fallback giúp dùng key/model còn khả dụng và tránh retry vô ích, nhưng không thể tạo thêm quota cho cùng organization.

Model 20B có thể trả lời kém sắc hơn 120B. Kết quả vẫn đi qua grounding, kiểm tra giá/quy cách và semantic review; nếu review bác hoặc mọi slot/model bị hạn chế, API tiếp tục trả lỗi có kiểm soát thay vì gửi nháp chưa kiểm tra.

## Tư vấn được kiểm tra tiếp sau fallback

Flow `advice_fallback_balanced_20261115.json` đã gọi hết 7 lượt: có 4 lượt 200, 3 lượt lỗi. Model 20B thực tế xuất hiện ở lượt nhắc 300g và hướng dẫn dầu gội; vì vậy đây không chỉ là thử ping model. Tuy nhiên nội dung chưa đạt: hỏi lại loại thịt, dùng rau răm làm món rau chính, và hướng dẫn nhãn dầu gội “không chứa dầu thô” thiếu thực tế. Không coi 200 là đạt chất lượng.

Các sửa tiếp theo:

- Đưa `category_hierarchy` thật vào evidence. Dựa vào nhánh gia vị để không dùng rau gia vị làm phần rau chính khi khách chỉ nhờ chọn rau; áp dụng cả hàng tìm mới và hàng nhớ từ lượt trước. Khách yêu cầu đúng loại rau gia vị vẫn được tìm.
- Câu hỏi làm rõ ở flow đã có ngữ cảnh được semantic review; nếu hỏi lại điều đã xác định thì chuyển sang soạn lời tư vấn với chủ đề/hàng đã biết.
- Hướng dẫn đọc nhãn liệt kê thuộc tính qua dấu phẩy được nhận diện theo cả câu, tránh bác nhầm phần thứ hai là công dụng của SKU. Vẫn chặn gán tính năng cho “loại này” và giữ ranh giới phủ định “nhưng”.

**398 test pass** trên workspace cho bản sửa tiếp theo; snapshot commit độc lập có **407 test pass**. Artifact `advice_fallback_shipping_20261115.json` ghi nguyên các lượt HTTP cuối, gồm cả lỗi; đọc trường `complete` trước khi sử dụng. Chưa nghiệm thu chất lượng như nhân viên.

Flow cuối chạy đủ 4 lượt và cả 4 đều HTTP 502. Log xác nhận đã thử đủ 5 key Qwen và 5 key 120B, đều có 429 trước khi chuyển sang tầng sau. Các lỗi kết thúc còn có evidence không khớp nhãn, thiếu số gram và semantic review bác câu không chọn hàng. Đây là thất bại tư vấn thực tế, không được báo thành công nhờ test hồi quy.
