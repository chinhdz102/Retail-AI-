# Đánh giá hội thoại tiếng Việt bằng text — 18/10/2026

## Phạm vi và phương pháp

- Gửi 25 lượt trong 15 đoạn hội thoại, toàn bộ yêu cầu dùng `language=vi`; gọi API Groq thật và runtime API đang chạy.
- Bao phủ chào hỏi, hỏi đáp ngắn, cách nói khẩu ngữ, tham chiếu qua nhiều lượt, sửa điều kiện, khuyến nghị, dữ kiện catalog, thông tin chưa có, yêu cầu ngoài khả năng agent và xác nhận giao dịch.
- Đối chiếu với catalog runtime demo gồm 6 sản phẩm. Dataset WinMart có 3.099 dòng (3.081 được chấp nhận, 18 cách ly), nhưng PostgreSQL chưa được benchmark trực tiếp.
- Chấm tự động cho grounding/an toàn, chọn tool, tham số, sản phẩm được nhắc và trường hợp không nên gọi tool. Đọc lại toàn bộ câu trả lời theo rubric: trả lời đúng lượt hiện tại, tự nhiên bằng tiếng Việt, tránh lặp, hỏi làm rõ khi mơ hồ, nói rõ khi dữ liệu thiếu.

## Kết quả

| Chỉ số | Kết quả |
|---|---:|
| HTTP thành công | 25/25 |
| Grounding và an toàn | 25/25 |
| Chọn đúng tool theo các ca có kiểm tra | 7/7 |
| Không gọi tool khi không cần | 10/10 |
| Tham số tool đúng | 2/2 |
| Lượt gọi LLM / lượt gọi tool | 23 / 14 |
| Test hồi quy backend | 124 passed |
| Ruff | All checks passed |

Trace ghi nhận 22 lượt model Qwen thành công. Một yêu cầu Qwen bị `BadRequest`; fallback `openai/gpt-oss-120b` hoàn tất thành công. Lượt chạy này chưa kích hoạt `openai/gpt-oss-20b`.

Latency API median là 11.861 giây khi pacing 12 giây đang bật. Pacing chiếm phần lớn thời gian: median API sau khi trừ pacing còn 458 ms; median một lần gọi model là 491 ms. Những lượt cần hai bước LLM có thể chờ khoảng 24 giây do pacing giữa các lần gọi, không phải do model xử lý lâu.

## Đọc lại hội thoại và quyết định dùng tool

| Tình huống | Quan sát |
|---|---|
| Chào hỏi, hỏi thăm, cảm ơn | Trả lời ngắn, cùng lối xưng hô mình–bạn, không gọi catalog tool. |
| Hỏi giá bằng câu khẩu ngữ hoặc `nhiu z` | Nhận ra Coca Zero và trả đúng giá; tìm sản phẩm rồi lấy snapshot để neo câu trả lời. |
| Hỏi tiếp kệ/tồn kho bằng “còn nằm kệ nào?”, “trong lon còn nhiều không?” | Giữ đúng sản phẩm qua lượt, dùng snapshot trực tiếp; trả đơn vị tồn kho là “lon”. |
| Khuyến nghị theo danh mục, ngân sách, độ cay | Dùng `recommend_products` và giữ điều kiện thành tham số; sửa ngân sách thì chạy lại theo mức mới. Không có món phù hợp thì nói rõ không tìm thấy. |
| Hỏi thành phần của món vừa chọn | Dùng `get_product_snapshot` cho món đã được grounding ở lượt trước. |
| Không có sản phẩm tham chiếu cho “cái đó” | Hỏi ngắn khách đang nói món nào, không liệt kê hoặc đoán giá. |
| Hỏi giá và kệ trong cùng câu | Trả cả hai trường đã được tra trong một câu. |
| Dị ứng, calories/đường, giờ đóng cửa hoặc khuyến mãi chưa có dữ liệu | Không suy diễn từ kiến thức nền; nêu rõ dữ liệu chưa xác minh/không có trong catalog. Dị ứng và thuộc tính dinh dưỡng không được xác nhận an toàn khi thiếu dữ liệu. |
| Hàng hết hạn, sản phẩm lạ, xác nhận chuyển khoản, gọi quản lý | Không bịa tồn kho/sản phẩm, không xác nhận giao dịch hoặc giả vờ đã gọi nhân viên; hướng khách tới nhân viên/quầy khi phù hợp. |

Nhìn chung các câu trả lời trực tiếp và tự nhiên cho một trợ lý cửa hàng demo. Bộ kiểm tra này không thấy câu nào bịa giá, kệ, tồn kho hay thuộc tính sản phẩm trong các ca đã chọn. Các câu trả lời an toàn đôi lúc vẫn mang tính khuôn mẫu (“mình chưa có dữ liệu đã xác minh”); bộ này chưa đo mức hài lòng thực tế của khách.

## Quy tắc chọn tool rút ra

1. Dùng `search_products` khi cần tìm/định danh món theo tên, tên gọi tắt hoặc cách viết khẩu ngữ.
2. Dùng `recommend_products` khi người mua đưa điều kiện lọc như danh mục, ngân sách, độ cay; các điều kiện cần được chuyển nguyên vẹn thành tham số.
3. Dùng `get_product_snapshot` cho giá, tồn kho, vị trí kệ, thành phần và khuyến mãi của một sản phẩm đã xác định. Với câu hỏi nối tiếp, tái sử dụng sản phẩm đã grounding thay vì tìm kiếm mở rộng.
4. Không gọi tool catalog cho xã giao, cảm ơn, thông tin ngoài catalog hoặc yêu cầu agent không có quyền thực hiện. Hỏi làm rõ khi đại từ không có sản phẩm tham chiếu; từ chối khẳng định thông tin chưa được xác minh.

## Căn cứ thiết kế bộ ca

- [ViWOZ: A Multi-Domain Task-Oriented Dialogue Systems Dataset For Low-resource Language](https://arxiv.org/abs/2203.07742): thiết kế ca đa lượt tiếng Việt, trạng thái hội thoại và cách diễn đạt lại.
- [A rich task-oriented dialogue corpus in Vietnamese](https://scholar.vnu.edu.vn/entities/publication/dbf88a93-0091-4784-9b25-0851e07a5f49): các miền ProductInfo, OrderInfo, Shipping, Chatchit cùng dialogue acts và slots.
- [The Berkeley Function Calling Leaderboard (BFCL)](https://proceedings.mlr.press/v267/patil25a.html): đánh giá riêng việc chọn hàm, điền tham số, xử lý trạng thái đa lượt và biết khi nào không gọi hàm.
- [Groq Local Tool Calling](https://console.groq.com/docs/tool-use/local-tool-calling): model phát tên tool/tham số; ứng dụng quyết định và thực thi tool.

## Giới hạn cần giữ trong cách diễn giải

Đây là một lượt coverage probe trên catalog demo, không phải tỷ lệ hallucination thống kê hay xác nhận chất lượng trên toàn bộ 3.081 SKU WinMart. Bộ này dùng câu tiếng Việt; chưa đo tỷ lệ lỗi nhận dạng tiếng nói, STT/TTS, streaming, hoặc hành vi trên PostgreSQL thật. Cần thêm hội thoại do người dùng Việt viết độc lập và đánh giá mù từ người mua/nhân viên để kết luận độ tự nhiên ngoài các ca đã dựng.

## Tệp kết quả

- Case: `evaluation/cases/vietnamese_text_conversation_v1.json`
- Trace API và câu trả lời: `evaluation/results/live_vietnamese_conversation_final_20261018.json`
