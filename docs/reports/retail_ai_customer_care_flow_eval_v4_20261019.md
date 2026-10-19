# Báo cáo đánh giá luồng hội thoại và chăm sóc khách hàng v4

**Ngày chạy:** 19/10/2026

**Phạm vi:** hội thoại văn bản tiếng Việt, backend agent và catalog demo. Không bao gồm STT/TTS.

## Kết luận

Các luồng sản phẩm, tư vấn, giỏ hàng và chống xác nhận giao dịch sai đã cải thiện rõ trong bộ dev. Bộ dev đạt **203/203 lượt**; bộ heldout chỉ đạt **26/49 lượt**. Vì vậy chưa thể kết luận agent xử lý ổn định các cách hỏi tự nhiên ngoài bộ dev. Có 23 assertion heldout thất bại; nhiều lỗi dây chuyền bắt đầu từ một câu tra sản phẩm hoặc cách hỏi tiếp nối.

Tất cả 252 lượt live đều nhận HTTP 200. Backend tests, P0 regression và shopping sandbox đều qua. PostgreSQL thật không kết nối được; các kết quả tra catalog ở đây dùng catalog demo sáu sản phẩm. Đây là giới hạn lớn khi suy rộng kết quả sang data WinMart hoặc cửa hàng thật.

## Kết quả

| Bài chạy | Quy mô | Kết quả assertion | HTTP | Latency lượt hội thoại |
|---|---:|---:|---:|---:|
| Live dev tiếng Việt | 41 hội thoại / 203 lượt | 203/203 (100%) | 203/203 | median 66 ms; p95 128 ms; max 10.49 s |
| Live heldout tiếng Việt | 10 hội thoại / 49 lượt | 26/49 (53.1%) | 49/49 | median 77 ms; p95 10.61 s; max 18.20 s |
| P0 backend regression | 8 lỗi × 5 lượt | 40/40 | — | Groq được mô phỏng |
| Shopping flow sandbox | 10 kịch bản, 49 API calls | 10/10 | 49/49 | median 1.37 ms; p95 54.14 ms |
| Product resolution offline | 28 truy vấn trên JSON | Recall@1/3/5: 100%; MRR 1.0 | — | median 14.02 ms; p95 159.86 ms |
| Pytest | Toàn bộ backend suite | **604 passed** | — | 20.31 s |
| Ruff / compileall | File backend và test đã sửa | Pass / Pass | — | — |

Shopping sandbox ghi nhận 0/2 xác nhận thanh toán sai và 0/2 rò rỉ chéo session. Bộ P0 dùng dịch vụ giả lập; hai số này không đại diện cho lưu lượng production.

Các kết quả JSON được lưu tại [dev live](../../evaluation/results/v4_final_dev_live_20261019.json), [heldout live](../../evaluation/results/v4_final_heldout_live_20261019.json), [P0](../../evaluation/results/v4_final_p0_20261019.json), [shopping sandbox](../../evaluation/results/v4_final_shopping_20261019.json) và [product resolution offline](../../evaluation/results/v4_final_product_resolution_20261019.json).

## Heldout: các lỗi còn lại

| Hội thoại | Lượt fail | Quan sát |
|---|---:|---|
| `heldout_combo_price_shelf_stock` | 6/6 | Câu gộp giá và chỉ đường không tìm được sản phẩm; các lượt tồn, hạn dùng, thêm giỏ và checkout sau đó mất ngữ cảnh. |
| `heldout_product_correction` | 2/6 | Không hiểu “nằm ở dãy nào?” và “giá realtime” sau khi đã chọn nước suối. |
| `heldout_multi_item_cart` | 2/6 | Các thao tác thêm/bớt/sửa số lượng qua; “chuẩn bị thanh toán thôi” không chuyển sang xác nhận, nên lượt xác nhận kế tiếp cũng thất bại. |
| `heldout_dialogue_region_and_typo` | 4/5 | “coca zerro” không khớp; câu miền Trung “kệ mô rứa” và các lượt theo ngữ cảnh tiếp theo bị ảnh hưởng. |
| `heldout_rank_without_sales_data` | 2/5 | Sau câu hỏi bán chạy, “có snack nào không cay?” và câu hỏi giá nối tiếp không trả đúng món từ catalog. |
| `heldout_expired_milk` | 2/5 | Câu khuyến mãi chưa xác minh và câu hỏi hạn in trên hộp không giữ được đúng ngữ cảnh sản phẩm. |
| `heldout_freshness_question` | 4/5 | Các cách hỏi “số giá này”, hàng còn bán hôm nay, vị trí trên sơ đồ và giá thời gian thực chưa được xử lý ổn định. |
| `heldout_promotion_and_no_new_arrival_claim` | 1/5 | Bot từ chối xác nhận hàng mới khi thiếu phiếu nhập, nhưng grader bắt substring “vừa nhập” trong câu phủ định và chấm fail. Đây là false positive của assertion, không phải tuyên bố rằng hàng đã nhập. |
| `heldout_customer_correction_and_budget` | 0/5 | Các lượt sửa nhóm hàng, độ cay và ngân sách đều qua. |
| `heldout_support_ticket` | 0/1 | Tạo yêu cầu hỗ trợ demo cho sự cố tính tiền hai lần; không gửi thông báo tới nhân viên thật. |

Heldout được chạy một lần sau khi hoàn tất thay đổi dev; không dùng các lỗi của bộ này để chỉnh luật tiếp trong lượt đánh giá này. Lỗi grader phủ định ở ca hàng mới vẫn được giữ trong tổng 26/49 để bảo toàn kết quả chạy nguyên bản.

## Thay đổi được kiểm tra

- Bổ sung parser cho cách mua tự nhiên như “cho mình 1 chai…”, xử lý số lượng khi bỏ bớt món, cùng các câu xem giỏ và trạng thái thanh toán.
- Giữ sản phẩm đã được tra/tư vấn làm ngữ cảnh cho câu hỏi giá, tồn, kệ, hạn dùng, thành phần và độ cay. Bổ sung regression cho Bim bim và thêm nước suối theo tham chiếu “đó”.
- Bổ sung nhận diện tư vấn theo nhóm hàng, ngân sách và độ cay; tránh trộn “sữa” trong “cà phê sữa” thành nhóm sữa riêng.
- Thêm chặn xác định cho yêu cầu nói dối rằng nhân viên đã nhận phản ánh: không gọi LLM, không tạo ticket giả, không báo đã liên hệ nhân viên.
- Sửa dữ liệu kỳ vọng giỏ hàng trong bộ dev từ chuỗi SKU sang map `{SKU: quantity}` để grader so sánh đúng với state API.

## Độ tin cậy và giới hạn

- Benchmark live dùng model Qwen cấu hình chính; **6/6 lần gọi ở dev và 9/9 lần ở heldout bị Groq rate limit**. Fallback `openai/gpt-oss-120b` xử lý thành công cả 15 lần. `openai/gpt-oss-20b` không được gọi. Kết quả live vì vậy không phải đánh giá Qwen đơn thuần.
- Runtime catalog được ép sang `demo` khi chạy benchmark vì PostgreSQL cấu hình trong môi trường timeout. Demo chỉ có sáu sản phẩm; file WinMart có 3.099 dòng, 3.081 dòng hợp lệ và 18 dòng bị quarantine. Recall offline đo bộ xếp hạng lexical trên JSON, **không xác minh truy vấn PostgreSQL, index, dữ liệu tồn cửa hàng hay nhập liệu DB**. Redis cũng chưa được xác minh.
- 203/203 dev là assertion tự động trên tập dev đã dùng để chỉnh sửa; không xem đây là độ chính xác tổng quát. Heldout 26/49 cho thấy còn lỗi hội thoại thực tế. Chưa có người chấm độc lập về độ tự nhiên, mức hữu ích hay tỷ lệ hallucination.
- Đây là text-only; không benchmark microphone, STT hoặc TTS. Endpoint SSE hiện phát các đoạn sau khi đã có toàn bộ câu trả lời, nên chưa đo TTFT và chưa chứng minh streaming token từ LLM.
- Support ticket là mô phỏng trong tiến trình backend. Không có tích hợp gọi nhân viên, CRM hoặc POS thật. Không chạy frontend build trong lượt này vì không sửa frontend.

## Tham chiếu phương pháp

- [τ-bench](https://arxiv.org/abs/2406.12045): đánh giá tương tác nhiều lượt user-agent-tool, trạng thái cuối và tuân thủ chính sách.
- [ToolSandbox](https://arxiv.org/abs/2408.04682): kiểm tra luồng công cụ có state và phụ thuộc qua nhiều lượt.
- [RAGAs](https://aclanthology.org/2024.eacl-demo.16/): tách chất lượng truy xuất, độ trung thành với ngữ cảnh và chất lượng câu trả lời; metric tự động không thay thế ground truth.

## Lệnh tái chạy chính

```powershell
$env:CATALOG_REPOSITORY = 'demo'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m evaluation.live_text_conversation_benchmark `
  --case-file evaluation/cases/vietnamese_text_sales_flow_v3_dev.json `
  --pace-seconds 9 `
  --output evaluation/results/v4_final_dev_live_20261019.json
```
