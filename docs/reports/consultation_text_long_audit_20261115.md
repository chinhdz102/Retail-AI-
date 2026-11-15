# Kiểm tra tư vấn bằng text thật — 15/11/2026

## Kết luận

**Chưa đạt mức nhân viên tư vấn thực sự.** Đã gọi backend thật với đủ 35 câu của người dùng và hai hội thoại dài 16 + 12 lượt. Không dùng mock hoặc STT để tạo kết quả live. Đã sửa nhiều lỗi ở truy xuất, trạng thái và kiểm tra câu trả lời; vẫn còn lỗi hiểu sản phẩm, suy công dụng và bỏ cuộc trong hội thoại dài. Không dùng HTTP 200 hoặc test unit làm bằng chứng rằng tư vấn đã tốt.

Bản full revision 2 có 49/63 lượt HTTP 200, 14 lượt HTTP 502. Chấm nội dung từng lượt: 20 đạt, 17 đạt một phần, 26 không đạt (bao gồm lượt lỗi). Đây là đánh giá của trợ lý, **không phải nghiên cứu người dùng độc lập**. Trong 49 lượt trả được, không phát hiện sai mã hàng, giá trên thẻ hoặc phép tính; điều đó không bảo đảm chọn đúng hàng hay lời khuyên đúng.

Những bản sửa sau full revision 2 được kiểm tra riêng; chưa có một bộ 63 lượt hoàn chỉnh chạy lại toàn bộ trên mã cuối cùng. Các số ở bảng dưới không được coi là điểm cuối của mã hiện tại.

## Phạm vi và kết quả giữ lại

- Backend thật: `http://127.0.0.1:8000`, tạo session riêng cho mỗi câu độc lập; giữ session cho luồng dài.
- Catalog cũ: `data/winmart_products.json`, SHA-256 `a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`; không thay catalog hoặc tạo lại dữ liệu bán trong đợt này.
- Model thực tế gồm Qwen `qwen/qwen3.8-27b` và fallback `openai/gpt-oss-120b`. Có trường hợp đổi model giữa các bước của cùng một lượt.
- Dùng đủ 35 câu gốc. Luồng dài kiểm tra đổi ngân sách, phủ định, đồ đã có, khẩu phần, đổi chủ đề, chai 1L, nới quy cách, chọn chốt; luồng gia dụng kiểm tra dầu gội, kích thước, bỏ gói lẻ, ngân sách mới, chuyển sang vệ sinh quanh mèo.
- Chấm theo: hiểu nhu cầu; giữ điều kiện; đúng loại/cách dùng; thông tin có cơ sở; giúp quyết định; lời nói tự nhiên và ngắn. Câu mơ hồ thiếu ngữ cảnh được phép hỏi nhận diện.

| Đợt | Lượt | HTTP 200 | Lỗi HTTP | Ghi chú |
|---|---:|---:|---:|---|
| 35 câu trước sửa | 35 | 29 | 6 | Đủ 35 câu |
| Luồng dài trước sửa | 28 | 10 | 18 | Đủ hai luồng |
| 35 câu trung gian | 35 | 32 | 3 | Chưa phải mã cuối |
| Full revision 2 | 63 | 49 | 14 | Đủ 35 + 28; còn nhiều lỗi nội dung |
| Hotfix 8 câu khó | 8 | 2 | 6 | Bị ảnh hưởng rate limit; không dùng để suy mức cải thiện |

Full revision 2: trung vị thời gian các lượt HTTP 200 là 2,718 giây, p95 5,981 giây. Có nhiều câu chỉ hỏi nhận diện nên không được xem đây là độ trễ của mọi câu tư vấn đầy đủ. Điều kiện quota, model fallback và nhịp gọi khác nhau giữa các đợt; chưa phải thí nghiệm A/B kiểm soát.

## Những lỗi tái hiện được

1. Gói phở có xốt bị diễn giải như một mặt hàng xốt riêng. Một lượt khác chọn xương heo nhưng khuyên xé/xào như thịt. Có lượt gọi bao tử cá basa là cá tươi thông thường.
2. Tìm `gạo` khi thiếu category đưa bánh gạo lên đầu; `thịt gà` đưa bánh/phở có nhắc thịt gà lên trước nguyên liệu. Đây là lỗi truy xuất/xếp hạng, không thể sửa chỉ bằng câu mở đầu lịch sự.
3. Planner tự chọn một loại rau rồi bộ lọc yêu cầu chính tên đó. Khi không có rau đó, cả nhóm rau bị bỏ, dù khách chỉ nhờ chọn một loại rau.
4. `Không cần quảng cáo công dụng` bị hiểu thành điều kiện của nhãn sản phẩm; các lượt sau báo không có dầu gội. Cần tách cách trả lời khỏi thuộc tính hàng.
5. Quy cách có thể mất khi câu nguồn rời cửa sổ lịch sử; lỗi provider liên tiếp cũng làm mất state nếu chỉ lưu kế hoạch sau câu trả lời thành công.
6. Hai món để chọn một bị cộng thành một giỏ quà; ở luồng khác chọn món cho cả bữa lại không in tổng do model gắn nhầm alternatives.
7. Câu hỏi bị lặp giữa advice và question. Có câu tự thêm `da bé`, tự upsell sau câu hỏi vị trí, và vẫn dùng giọng `trong danh sách`.
8. Bộ kiểm tra từng bác lời khuyên chung về đọc nhãn như thể đó là công dụng đã gán cho SKU. Ngược lại, reviewer vẫn có lúc bỏ lọt mô tả sai cách chế biến.
9. Một lỗi định dạng/evidence có thể dùng hết lượt sửa, khiến sai nội dung phát hiện ở bước review không còn cơ hội sửa.
10. Có HTTP 429 và trường hợp hết lượt thử cả hai model/năm vị trí key. Nhưng **không phải mọi HTTP 502 đều do quota**: log còn xác nhận lỗi render, evidence và review.

## Đã thay đổi trong mã

- Thêm ledger yêu cầu theo phiên với thao tác set/remove và câu nguồn từ khách; bỏ cập nhật không có câu nguồn khớp. Xóa ledger cũ khi đổi chủ đề. Không dùng lời model tự viết làm nguồn yêu cầu.
- Giữ câu nguồn quy cách đã xác minh từ kế hoạch trước, dù lượt đó không còn trong lịch sử ngắn.
- Lưu kế hoạch đã kiểm tra khi bước soạn/review gặp lỗi, để các yêu cầu không biến mất sau chuỗi lỗi. Chưa bao giờ lưu một draft lỗi thành câu trả lời thành công.
- Phân biệt bộ lọc AND `required_phrases` và OR `any_phrases`; tách các query gộp bằng dấu phẩy, đưa quy cách về bộ lọc riêng.
- Ưu tiên category đúng và loại hàng chính/vị trí từ trong tên, giảm trường hợp lấy thành phần/hương vị làm loại hàng. Ưu tiên gói lẻ trước thùng/lốc; không chia thùng thành SKU chai lẻ giả.
- Backoff trong cùng category khi loại cụ thể do planner tự chọn không có hàng. Không backoff loại được khách nói rõ; vẫn giữ hãng, quy cách, biến thể và giá.
- Tách lựa chọn thay thế với giỏ mua chung; ép giỏ bữa ăn tuân thủ tổng ngân sách và tính tổng bằng code.
- Loại câu hỏi trùng; sửa giới hạn ngôn ngữ log/danh sách; kiểm tra giả định tuổi khách chưa nói và upsell không liên quan. Loại tên SKU chính xác bị chép lặp trong reason mà vẫn giữ phần cách dùng.
- Hiệu chỉnh reviewer: không bắt phải chọn SKU khi chưa có bằng chứng an toàn; không suy hương hoa/thiên nhiên là an toàn cho mèo; không coi gợi ý `mình chọn` là đã đặt hàng; phân biệt gói mua với phần ăn.
- Tách tối đa một lượt sửa render và một lượt sửa nội dung: tối đa ba draft, vẫn có giới hạn. Không bỏ kiểm tra để tăng tỷ lệ HTTP 200.

Các guard này có giới hạn: keyword/ranking không thay thế hiểu ngữ nghĩa; ledger vẫn phụ thuộc planner diễn giải đúng và không phải mọi yêu cầu đã trở thành bộ lọc có thể thi hành bằng code.

## Kiểm chứng mã cuối

`CATALOG_REPOSITORY=demo python -m pytest tests/test_consultation.py tests/test_consultation_review.py tests/test_groq_services.py tests/test_retail_api.py -q --tb=short`

**296 passed**. Test hồi quy gồm mất lịch sử, set/remove có nguồn, lưu state khi API lỗi, category so với hương vị, giữ loại khách chỉ định khi backoff, AND/OR, tổng giỏ, hỏi lặp và ngân sách sửa lỗi render/review. Fixture demo tách khỏi catalog thật; production vẫn dùng catalog cũ.

Kiểm tra live trên mã sau sửa truy xuất: câu bữa tối 200 nghìn đã chọn 300g thịt heo xay 42.570đ, cải bó xôi 300g 20.720đ và hộp trứng 34.000đ, tổng **97.290đ**, hỏi nhà đã có gạo chưa. Đây là một kết quả tốt hơn có chứng cứ; không suy ra mọi bữa ăn/ngành hàng đều đạt. Artifact: `artifacts/advice_shipping_checks_20261115.json`.

Đợt kiểm tra dầu gội sau đó tiếp tục gặp lỗi evidence/công dụng và provider. Giữ nguyên artifact cả đợt dừng giữa chừng; không đánh dấu complete cho luồng chưa chạy xong.

Luồng `advice_household_final_20261115.json` đã chạy đủ 12 lượt nhưng chỉ 4 lượt trả được. Lượt 4 chọn được Thái Dương 480ml/114.900đ đúng quy cách và ngân sách; lượt 6 nói đúng điều kiện còn thiếu; các lượt đổi ngân sách/nới chai và phần vệ sinh quanh mèo phần lớn gặp 502. Đây là bằng chứng hệ thống vẫn chưa ổn định ở trạng thái dài.

## Nghiên cứu đối chiếu và cách áp dụng

- [RealWorldShop, 2026](https://arxiv.org/html/2609.38974v1): phân biệt chất lượng từng lượt với thành công cả phiên; state manager, điều khiển hành động và kiểm tra khi chạy. Áp dụng vào ledger, đổi chủ đề và kiểm tra luồng dài. Chưa tái tạo phần huấn luyện SFT/RL của paper.
- [CARTBench, 2026](https://arxiv.org/html/2611.05862v1): chấm theo tiêu chí có bằng chứng, giữ các điều kiện chưa biết. Áp dụng vào bảng chấm từng lượt và tách đúng dữ liệu khỏi chất lượng lời khuyên. Chưa có chuyên gia độc lập chấm dự án.
- [CHIQ, EMNLP 2024](https://aclanthology.org/2024.emnlp-main.135/): xử lý mơ hồ trong lịch sử trước khi viết truy vấn. Áp dụng một phần qua need độc lập và state; chưa huấn luyện mô hình CHIQ.
- [ICR, EMNLP 2025](https://aclanthology.org/2025.emnlp-main.496/): gắn làm rõ với viết lại truy vấn. Dùng để thiết kế kiểm tra câu hỏi có ích cho quyết định; chưa triển khai toàn bộ vòng lặp ICR.
- [Diable, Findings ACL 2023](https://aclanthology.org/2023.findings-acl.615/): cập nhật state bằng thao tác thay vì viết lại toàn bộ. Áp dụng ý tưởng set/remove; không lấy kết quả MultiWOZ làm kết quả bán lẻ của dự án.
- [Wizard of Shopping, ACL 2025](https://aclanthology.org/2025.acl-long.641/): hội thoại gắn lộ trình tìm hàng. Dùng để thiết kế nhánh sửa yêu cầu/chọn chốt trong luồng dài.
- [Groq reasoning docs](https://console.groq.com/docs/reasoning): thử reasoning low ở planner/reviewer và GPT-OSS120B medium trên một số câu. Thử nhỏ vẫn có lỗi và tốn thêm thời gian; **chưa chuyển cấu hình chính chỉ dựa trên các thử này**.

Không có paper nào chứng minh bản ứng dụng này đã đạt mức nhân viên thật. Kết quả paper và kết quả đo tại đây được giữ riêng.

## Phần chưa đạt và kế hoạch nghiệm thu tiếp

1. **Chưa đạt:** hiểu loại hàng/cách dùng ở mọi nhóm; phân biệt chỉ dẫn cách trả lời với thuộc tính sản phẩm; giữ yêu cầu khi chuyển chủ đề và nới điều kiện.
2. Cần lớp kiểm tra phù hợp của từng lựa chọn dựa vào loại hàng/thực thể chính và yêu cầu đang hoạt động, với dữ liệu phản ví dụ rộng. Không vá riêng thịt lẩu hoặc một SKU.
3. Khi không có bằng chứng cho công dụng tóc/da, phải đưa cách chọn hữu ích hoặc hỏi một chi tiết quyết định, không cố bán rồi bị guard bác, cũng không đọc mẫu không có trong danh mục.
4. Cần chạy lại bộ 63 lượt trên cùng phiên bản mã, quota đủ và ghi trace theo từng giai đoạn; thêm bộ câu giữ kín ở ngành hàng khác. Chưa đo được mức cải thiện tổng thể đáng tin cậy của mã cuối.
5. Nghiệm thu cần không có lỗi sai loại hàng/cách dùng nguy hiểm, giữ mọi ràng buộc cứng, không bịa SKU/giá/tồn, và con người đánh giá lời khuyên hữu ích qua nhiều phiên độc lập. Một lần chạy tốt chưa đủ.

## Dấu vết kiểm tra

- `artifacts/advice_questions_before_20261115.json`: đủ 35 câu ban đầu.
- `artifacts/advice_long_before_20261115.json`: hai luồng dài trước sửa.
- `artifacts/advice_questions_intermediate_20261115.json`: 35 câu bản trung gian.
- `artifacts/advice_full_revision2_20261115.json`: bộ 63 lượt hoàn chỉnh trước đợt sửa cuối.
- `artifacts/advice_hotfix_questions_20261115.json`, `advice_hotfix_long_20261115.json`: đợt bị ảnh hưởng quota, có ghi complete.
- `artifacts/advice_shipping_checks_20261115.json`: kiểm tra HTTP sau sửa truy xuất; đọc complete trước khi dùng.
- `artifacts/advice_reviews_20261115.json`: chấm nội dung từng câu và từng lượt.
- `artifacts/advice_summary_20261115.json`: thống kê tự động, kiểm tra giá/mã/tổng.
- `artifacts/advice_source_manifest_20261115.json`: hash catalog và mã cuối.
- Các probe reasoning được giữ để xem cả kết quả không thành công, không chọn riêng mẫu đẹp.

## Chấm từng lượt: 35 câu trước sửa

| Lượt | Đánh giá | Lý do |
|---|---|---|
| q01:1 | pass | Hỏi khu vực/mục đích để xác định nhóm hàng. |
| q02:1 | partial | Chọn được món sáng nhưng dài, suy diễn mức no và chưa phân biệt cách làm nóng. |
| q03:1 | fail | HTTP 502. |
| q04:1 | fail | Gợi hạt nguyên chất cho trẻ khi chưa biết tuổi; chưa chọn được hàng. |
| q05:1 | partial | Hai phương án quà bị cộng thành giỏ; suy diễn bao bì cao cấp. |
| q06:1 | fail | HTTP 502. |
| q07:1 | fail | Tự thêm hàng và gán độ bền/khả năng lau bề mặt nhạy cảm cho giấy ăn. |
| q08:1 | fail | Gọi gói phở kèm xốt là một phần xốt riêng; bữa tối không được lập hợp lý. |
| q09:1 | partial | Câu hỏi bị lặp, đưa danh sách thay vì chọn có lý do cụ thể. |
| q10:1 | fail | HTTP 502. |
| q11:1 | pass | Chưa có ngữ cảnh sản phẩm nên hỏi nhận diện. |
| q12:1 | pass | Hỏi sản phẩm đang so sánh, không bịa hàng. |
| q13:1 | pass | Hỏi nhóm hàng khi chưa có ngữ cảnh. |
| q14:1 | pass | Hỏi tên hai SKU để so sánh. |
| q15:1 | partial | Không bịa hãng giặt sạch hơn, nhưng chưa giúp chọn thực tế. |
| q16:1 | pass | Hỏi đối tượng cần so sánh. |
| q17:1 | pass | Hỏi hai sản phẩm cụ thể. |
| q18:1 | pass | Không tự chọn sản phẩm khi chưa có ngữ cảnh. |
| q19:1 | pass | Hỏi nhóm cần so sánh. |
| q20:1 | fail | HTTP 502. |
| q21:1 | fail | HTTP 502. |
| q22:1 | fail | HTTP 502. |
| q23:1 | partial | Có hàng đúng nhãn không đường nhưng tự đưa thùng/lốc lớn, nhiều lựa chọn. |
| q24:1 | pass | Hỏi loại vật dụng khi chưa xác định. |
| q25:1 | partial | Hỏi nhận diện nhưng tự thu hẹp nhóm chăm sóc cá nhân. |
| q26:1 | pass | Không tự đặt vị trí khu kệ chưa xác minh. |
| q27:1 | partial | Hỏi thương hiệu đúng hướng nhưng xưng hô vụng. |
| q28:1 | pass | Hỏi nhãn hiệu/bao bì thay vì đoán lần mua cũ. |
| q29:1 | pass | Không bịa vị trí; câu xưng hô còn vụng. |
| q30:1 | pass | Hỏi chai một lít của mặt hàng nào. |
| q31:1 | pass | Hỏi mặt hàng cần thay thế. |
| q32:1 | partial | Câu hỏi đúng nhưng cách xưng hô thiếu tự nhiên. |
| q33:1 | pass | Hỏi nhóm hàng cần mua. |
| q34:1 | pass | Hỏi sản phẩm dùng chung nào. |
| q35:1 | pass | Không suy màu bao bì thành SKU đã mua trước. |

## Chấm từng lượt: Bộ 63 lượt revision 2

| Lượt | Đánh giá | Lý do |
|---|---|---|
| q01:1 | pass | Hỏi khu vực/mục đích để xác định nhóm hàng. |
| q02:1 | fail | Nói bánh bao mua về ăn ngay không cần chế biến dù thiếu chứng cứ ăn liền. |
| q03:1 | fail | HTTP 502. |
| q04:1 | pass | Hỏi tuổi trước khi chọn đồ ăn cho trẻ. |
| q05:1 | fail | HTTP 502. |
| q06:1 | partial | Hàng tiện dùng hợp chủ đề nhưng vẫn dài và suy cách làm nóng bánh bao. |
| q07:1 | fail | Gán nước lau nhà dùng lau kính, chưa có nhãn chứng minh. |
| q08:1 | fail | Chọn xương heo rồi nói là thịt để xé/xào nhanh. |
| q09:1 | partial | Có SKU cụ thể, vẫn tự gán giảm ngứa và đưa nhiều dòng điều trị không được hỏi. |
| q10:1 | pass | Hỏi số người trước khi lập đồ ăn cả tuần. |
| q11:1 | pass | Chưa có ngữ cảnh sản phẩm nên hỏi nhận diện. |
| q12:1 | pass | Hỏi sản phẩm đang so sánh, không bịa hàng. |
| q13:1 | pass | Hỏi nhóm hàng khi chưa có ngữ cảnh. |
| q14:1 | pass | Hỏi tên hai SKU để so sánh. |
| q15:1 | fail | HTTP 502. |
| q16:1 | pass | Hỏi đối tượng cần so sánh. |
| q17:1 | pass | Hỏi hai sản phẩm cụ thể. |
| q18:1 | pass | Không tự chọn sản phẩm khi chưa có ngữ cảnh. |
| q19:1 | pass | Hỏi nhóm cần so sánh. |
| q20:1 | fail | Tự đổi sang da bé, hỏi nhiều chi tiết và dạng thanh/bột không rõ. |
| q21:1 | partial | Không bịa ít ngọt nhưng hỏi dồn loại bánh và mức đường; chưa giúp chọn. |
| q22:1 | fail | HTTP 502. |
| q23:1 | partial | Có hàng đúng nhãn không đường nhưng tự đưa thùng/lốc lớn, nhiều lựa chọn. |
| q24:1 | pass | Hỏi loại vật dụng khi chưa xác định. |
| q25:1 | partial | Hỏi nhận diện nhưng tự thu hẹp nhóm chăm sóc cá nhân. |
| q26:1 | partial | Không bịa kệ nhưng tự upsell thêm nước giặt và lặp lời mời. |
| q27:1 | partial | Thừa đoạn hướng dẫn chọn hộp màu xanh trước câu hỏi thương hiệu. |
| q28:1 | partial | Hỏi nhận diện đúng nhưng hứa kiểm tra tồn chính xác khi chỉ có snapshot. |
| q29:1 | partial | Hỏi cửa hàng nhưng hứa chỉ vị trí chưa có công cụ/bằng chứng. |
| q30:1 | pass | Hỏi chai một lít của mặt hàng nào. |
| q31:1 | pass | Hỏi mặt hàng cần thay thế. |
| q32:1 | pass | Hỏi tên/loại sản phẩm, không đoán quảng cáo TikTok. |
| q33:1 | pass | Hỏi nhóm hàng cần mua. |
| q34:1 | pass | Hỏi sản phẩm dùng chung nào. |
| q35:1 | pass | Không suy màu bao bì thành SKU đã mua trước. |
| long-dinner:1 | fail | HTTP 502, không trả được tư vấn. |
| long-dinner:3 | fail | HTTP 502, không trả được tư vấn. |
| long-dinner:4 | fail | HTTP 502, không trả được tư vấn. |
| long-dinner:8 | fail | HTTP 502, không trả được tư vấn. |
| long-dinner:9 | fail | HTTP 502, không trả được tư vấn. |
| long-dinner:14 | fail | HTTP 502, không trả được tư vấn. |
| long-dinner:15 | fail | HTTP 502, không trả được tư vấn. |
| long-household:2 | fail | HTTP 502, không trả được tư vấn. |
| long-household:5 | fail | HTTP 502, không trả được tư vấn. |
| long-household:9 | fail | HTTP 502, không trả được tư vấn. |
| long-dinner:2 | fail | Gọi xương heo là thịt mềm để cắt lát/xào, sai dạng hàng. |
| long-dinner:5 | partial | Đúng thịt nạc/rau, thiếu tổng khi đang lập bữa. |
| long-dinner:6 | partial | Giữ ngân sách, lượng và tổng đúng; còn dài và tư vấn rau mầm sống thiếu cân nhắc. |
| long-dinner:7 | partial | Không thêm gia vị nhưng thiếu tổng phương án. |
| long-dinner:10 | fail | Báo không có trà không đường dù bản cũ có lốc; lọc cứng thiếu bằng chứng không ga. |
| long-dinner:11 | partial | Không có chai riêng dưới giá nhưng tiếp tục nói kiểu danh sách. |
| long-dinner:12 | partial | Không tự đổi chai nhỏ nhưng dùng lời văn truy xuất, thiếu hướng chọn hữu ích. |
| long-dinner:13 | partial | Không thực sự so sánh lựa chọn gần nhất mà hỏi xin tìm tiếp. |
| long-dinner:16 | fail | Nói đã chọn chai dù các lượt chọn trước bị lỗi; không có SKU cuối. |
| long-household:1 | partial | Chỉ gợi tiêu chí, chưa giúp chọn hàng và đẩy sang nhân viên quầy. |
| long-household:3 | fail | Hiểu không quảng cáo công dụng thành điều kiện của sản phẩm. |
| long-household:4 | fail | Báo thiếu dầu gội >=400ml dù có Thái Dương480ml114900; trạng thái/filter sai. |
| long-household:6 | fail | Giữ sai tiêu chí không quảng cáo và bỏ cuộc. |
| long-household:7 | fail | Không chọn được hàng sau khi cho phép chai nhỏ dưới100k. |
| long-household:8 | fail | Không chốt được dù còn hàng chai nhỏ phù hợp mức giá. |
| long-household:10 | pass | Tách mèo khỏi khu lau, làm theo nhãn, thông thoáng và đợi khô. |
| long-household:11 | partial | Giữ mèo khỏi khu lau nhưng tự khuyên giấm/baking soda chưa biết bề mặt. |
| long-household:12 | pass | Giữ tư vấn chung, không bán thêm và tách mèo khỏi khu lau. |
