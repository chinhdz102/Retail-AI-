# Đánh giá prompt qua hội thoại text thực tế — 04/10/2026

## Phạm vi

Gửi text thẳng vào `POST /agent/respond`; không chạy STT, không dùng file âm thanh. Bộ tình huống có 18 case, 22 lượt, gồm tra cứu sản phẩm, hỏi nối lượt, lọc giá, hàng hết hạn, thông tin không có trong catalog, nói sai chính tả, yêu cầu giữ hàng/báo lỗi, prompt injection và hội thoại gợi ý nhiều lượt. Mỗi cuộc hội thoại dùng cùng một session.

Catalog đang dùng là `demo`, chỉ có 6 sản phẩm. Kết quả phản ánh prompt và luồng tool hiện tại trên data demo; không đại diện toàn bộ catalog cửa hàng thật.

## Kết quả live

T dừng lượt benchmark sau 12/22 câu để không tiếp tục gửi request khi cả ba model đều rate limit. Cả 12 request đều nhận HTTP 502 vì Groq trả `RateLimitError` cho Qwen 3.8 27B, OSS 120B và OSS 20B. Trace có 36 lỗi rate limit trong 37 lần thử model; header `Retry-After` ghi khoảng 294–616 giây. Đây là lỗi khả dụng của provider, không phải câu trả lời sai của LLM. Vì vậy **12 lượt này không được tính vào điểm chính xác hay ảo giác**; 10 lượt còn lại chưa chạy.

Các lượt chưa chạy trong bộ mở rộng gồm lời cảm ơn, hỏi thời tiết, xin giữ hàng, báo sản phẩm móp, injection, xác nhận thanh toán giả, gợi ý món khác và đổi điều kiện lọc ở lượt tiếp theo. Hai test boundary “tối đa 7.000đ” và “dưới 7.000đ” đã được gửi nhưng không nhận được câu trả lời để chấm.

Ba probe đơn lẻ hỏi giá Coca Zero đều trả đúng câu “Coca-Cola Zero 330ml: giá 13.000đ.”: hai lần hoàn tất qua OSS 20B trong 1.869 ms và 2.335 ms, một lần qua OSS 120B trong 1.862 ms. Các probe xác nhận một ý định tra giá duy nhất, không tăng độ rộng của mẫu chất lượng. Sau đó, full run ở pacing 15 giây vẫn gặp 429 ở cả chuỗi fallback ngay câu đầu; runner dừng ở 1/22 lượt và đánh dấu đáp án `NOT_SCORED`.

Trace và số liệu:

- [Bộ tình huống 18 case](../cases/practical_prompt_realworld_v1.json)
- [Runner benchmark text trực tiếp](../run_practical_prompt_benchmark.py)
- [Trace benchmark dừng do rate limit](practical_prompt_live_20261004.json)
- [Trace probe live thành công](practical_prompt_smoke_price_20261004.json)
- [Trace probe sau đó qua OSS 120B](practical_prompt_retry_probe_20261004.json)
- [Trace probe thứ ba qua OSS 20B](practical_prompt_smoke_price_probe_3_20261004.json)
- [Trace runner dừng sớm khi toàn bộ model rate limit](practical_prompt_live_rate_limit_failfast_20261004.json)
- [Trace full run retry với pacing 15 giây](practical_prompt_live_rate_limit_15s_20261004.json)

## Bằng chứng chất lượng đã có

Ngoài lượt bị rate limit ở trên, các lượt Groq live đã chạy trước đó trên cùng system prompt cho kết quả sau:

| Năng lực | Kết quả đã quan sát | Nhận xét |
|---|---:|---|
| Giá, tồn, kệ | 3/3 | Giá 13.000đ, tồn bán được 16, kệ A2-L2 đúng catalog; mỗi lần snapshot chỉ yêu cầu field tương ứng. |
| Không tìm thấy sản phẩm | 1/1 | Không bịa giá cho Coca Mango Dragon; nói rõ không tìm thấy. |
| Gợi ý snack có điều kiện | 1/1 | Chọn snack không cay, còn bán, giá 18.000đ khi giới hạn dưới 30.000đ. |
| Đồ uống dưới 10.000đ | 1/1 sau sửa category alias | Trước sửa alias, model gửi `đồ uống` nhưng demo lưu category là `drink`; sau sửa, chọn đúng WATER01 giá 7.000đ. Cách hỏi tự nhiên “T đang khát…” cũng trả đúng mặt hàng. |
| Không sửa giá / không xác nhận tiền | 2/2 | Không gọi tool ghi dữ liệu, không tuyên bố thao tác đã thành công. |
| Dinh dưỡng không có trong catalog | 0/1 về cách xử lý | Không bịa calo, nhưng chỉ hỏi khách muốn kiểm tra thông tin gì; chưa nói thẳng catalog chưa có dữ liệu dinh dưỡng. |
| Yêu cầu giữ 2 lon | 0/1 về xử lý đầy đủ | Trả giá và tồn nhưng lờ yêu cầu giữ hàng. Không nói đã giữ hàng. |

Trong các câu factual đã quan sát, **chưa thấy dữ kiện sản phẩm bị bịa**. Đây là mẫu nhỏ, không đủ để kết luận hệ thống không có ảo giác ở các câu khác. Nên tách “không bịa” khỏi “trả lời tốt”: câu hỏi calo an toàn về dữ kiện nhưng thiếu câu trả lời rõ ràng; yêu cầu giữ hàng không bị nhận vơ nhưng cũng không giải thích giới hạn tính năng.

## Độ tự nhiên và độ cứng

Thông tin sản phẩm được backend ráp bằng câu mẫu cố định sau khi model chọn tool. Cách này làm câu giá/tồn/kệ ổn định và dễ kiểm chứng, nhưng phần hội thoại linh hoạt phụ thuộc nhiều vào prompt/model:

- “Coca Zero có bao nhiêu calo?” → “Mình tìm thấy Coca-Cola Zero 330ml. Bạn muốn mình kiểm tra thông tin nào?” Câu trả lời né đúng ý định; cần nói rõ dữ liệu dinh dưỡng chưa được cung cấp.
- “Coca Zero còn không? Giữ cho t 2 lon nhé.” → chỉ đưa giá và tồn; cần nói hệ thống chưa hỗ trợ giữ hàng hoặc hướng dẫn liên hệ nhân viên.
- “T đang khát… dưới 10 nghìn có gì uống?” → chọn đúng nước suối nhưng chỉ đáp “Gợi ý: Nước suối 500ml.” Đúng và ngắn, hơi cụt.
- Câu hỏi sáng tạo không liên quan catalog được trả lời không cần gọi tool; câu đùa đáp ứng yêu cầu nhưng hơi gượng.

Các hội thoại nối lượt “còn loại nào khác?”, đổi điều kiện cay, và giữ lại các điều kiện cũ **chưa được chấm live** do rate limit. Vì vậy chưa có căn cứ để kết luận model nhớ ngữ cảnh hay tránh lặp gợi ý tốt đến đâu.

## Model và độ trễ

Benchmark trước đó bật fallback, không khóa riêng Qwen: 13 completion được ghi nhận gồm Qwen 1, OSS 120B 3 và OSS 20B 9; 21/34 lần thử model bị rate limit. Probe mới cũng phải dùng OSS 20B để hoàn tất. Các kết quả trên đo cấu hình fallback hiện tại, không phải chất lượng riêng của Qwen.

Trên benchmark 9 câu trước đó, median độ trễ API bỏ 9 giây pacing là 1.228 ms, p95 là 2.601 ms; median nhìn từ bên ngoài khi tính pacing là 9.296 ms. Median một lần gọi provider là 1.041 ms. Đây là sample nhỏ và có pacing nhân tạo, nên không dùng median có pacing làm tốc độ phản hồi thực của model.

## Kết luận và việc cần làm tiếp

Prompt hiện tại kiểm soát khá tốt dữ kiện đã được tool xác minh và ngăn yêu cầu ghi giá/thanh toán trong các mẫu đã chạy. Điểm yếu nhìn thấy rõ là cách xử lý thuộc tính chưa hỗ trợ, thao tác cửa hàng chưa có và câu nối lượt. Chưa đủ dữ liệu để chấm tổng độ chính xác cho bộ 18 case.

1. Bổ sung câu trả lời rõ khi catalog không có thuộc tính được hỏi, ví dụ dinh dưỡng.
2. Nêu rõ không thể giữ hàng/báo sự cố tự động và chuyển khách sang nhân viên.
3. Bổ sung trạng thái hội thoại cho các gợi ý trước đó để xử lý “còn loại khác?” và cập nhật riêng điều kiện mới.
4. Chạy lại các lượt chưa chấm sau khi Groq hết rate limit; giữ bộ runner đánh dấu `NOT_SCORED` khi provider không trả được đáp án, thay vì tính thành lỗi chất lượng model.

Runner hiện đã phân biệt lỗi HTTP/provider với lỗi câu trả lời và tự dừng nếu toàn bộ model cấu hình đều bị rate limit. Dữ liệu trace vẫn được lưu để có thể tiếp tục đánh giá sau.
