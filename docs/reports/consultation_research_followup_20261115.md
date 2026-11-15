# Tư vấn, truy xuất và voice — kiểm tra tiếp ngày 15/11/2026

> Đối chiếu với paper và hệ thống retail lớn: [architecture_research_comparison_20261115.md](architecture_research_comparison_20261115.md).

**Kết luận: chưa nghiệm thu tư vấn ở mức nhân viên thực sự.** Đã sửa các nguyên nhân code có bằng chứng và chạy lại text thật; vẫn tái hiện lời quảng cáo không có căn cứ, quyết định mua quà tùy tiện và lỗi 502. Không dùng số unit test pass hoặc HTTP 200 làm điểm chất lượng tư vấn.

Đây là phần tiếp của [báo cáo kiểm tra khách hàng](consultation_customer_review_20261115.md). Catalog cũ vẫn có 3.081 hàng, SHA-256 `a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`. Không sửa catalog hoặc thêm SKU để làm ca kiểm tra qua.

## 1. Nguyên nhân và sửa đổi đã thực hiện

### Sai loại hàng khi tìm không có kết quả

Trước đây, truy vấn không ra kết quả có thể bị cắt đuôi mù: `thịt ba chỉ heo` thành `thịt ba chỉ`, rồi lấy ba chỉ bò. Bản sửa phân biệt loại khách đã chỉ định với nguyên liệu do planner tự đề xuất. Tên con vật, loại thịt, biến thể và quy cách khách nêu không được tự bỏ khi backoff. Ca hồi quy áp dụng cả thịt, nước tẩy và sữa tắm.

Các tên vùng miền nằm trong `data/vietnamese_product_terms.json`, có URL nguồn và phạm vi ngành hàng. Hiện gồm heo/lợn, ba chỉ/ba rọi, rửa chén/rửa bát, mì chính/bột ngọt và lạc/đậu phộng. Đây là từ vựng truy xuất; không sửa transcript STT, không sinh đáp án và không thay nhãn/brand/quy cách. Trace LLM trước đó từng khẳng định ba chỉ và ba rọi là hai phần thịt khác nhau, nên không lấy suy đoán của LLM làm nguồn từ đồng nghĩa.

### Ngành hàng do planner đoán làm mất hàng phù hợp

Trace da nhạy cảm cho thấy planner gắn `sữa tắm` vào `Chăm Sóc Cá Nhân Khác`. Hai bước lọc/rank sau đó biến gợi ý này thành điều kiện loại bỏ, khiến sản phẩm ở `Chăm Sóc Da` biến mất. LLM chỉ thấy nhiều loại tắm gội trẻ em rồi suy từ “không cay mắt” ra hợp da nhạy cảm.

Bản sửa giữ các sản phẩm có đúng loại hàng ở nhiều ngành, dùng tên loại và taxonomy thực của sản phẩm để phân biệt hàng chính với nhân/hương vị. Ngành hàng chỉ trở thành giới hạn khi được yêu cầu rõ hoặc có phạm vi từ vựng đã xác thực. Test dùng catalog cũ xác nhận `TLM_10170932` — Sữa tắm Dove chăm sóc da nhạy cảm 500g — xuất hiện lại trong candidates. Ca giữ quy cách 1L/không mùi kiểm tra chéo qua ngành vệ sinh; bánh/khăn chỉ chứa tên hàng trong nhân hoặc mùi vẫn bị loại.

### Top-k che mất biến thể và hàng đã từ chối chiếm chỗ

Loại đồ đã có/đã từ chối trước top-k. Chọn ứng viên dùng đa dạng tên/brand/taxonomy theo MMR từ toàn bộ tập đã lọc, giữ một hàng liên quan và một lựa chọn đóng gói giá thấp; không so trực tiếp giá/kg với giá/gói. Từ trong yêu cầu khách được dùng làm tín hiệu xếp hạng để biến thể hiếm trên nhãn không bị nhiều quy cách gần trùng lấn hết. Tín hiệu này không chứng minh tính năng của hàng không ghi trên nhãn.

### Model và reviewer tự thêm thông tin

Giữ `requirement_quotes` từ lời khách đã xác thực, truyền sang composer và reviewer. Không cho tóm tắt do model tự viết biến “da em nhạy cảm” thành mua cho “bé”. Kiểm tra dấu tiếng Việt để “bề mặt” không bị hiểu thành “bé”.

Sửa các false positive: câu hỏi khách đã có sẵn chổi/khăn không phải khẳng định cửa hàng có hàng; lời nhắc lại nhu cầu tìm đồ dịu nhẹ không tự động là khẳng định SKU dịu nhẹ. Lời hứa giảm khó chịu/kích ứng vẫn cần căn cứ của chính sản phẩm. Bổ sung chặn lời hứa no lâu không có nhãn chứng minh.

Thông báo sửa lý do hiện giữ dấu nguyên văn của cụm bị chặn. Trước đó model nhận phản hồi dạng `du dung cho mot bua`; nay nhận `đủ dùng cho một bữa`. Khi lý do không có căn cứ, vòng sửa được chọn lại sản phẩm; sau hai lần không sửa được khẳng định SKU, có thể chuyển sang hướng dẫn chung, nhưng vẫn phải qua kiểm tra. Cơ chế này chưa bảo đảm luôn tránh được 502.

## 2. Nghiên cứu và cách áp dụng

- [Khảo sát self-correction, TACL 2024](https://aclanthology.org/2024.tacl-1.78/): không coi reviewer của cùng nhóm model là nguồn sự thật. Áp dụng phản hồi bên ngoài từ nhãn, phép tính, lời khách có trích dẫn và từ vựng có nguồn. Chưa huấn luyện/fine-tune model trong đợt này.
- [MMR, SIGIR 1998](https://www.cs.cmu.edu/afs/cs/Web/People/jgc/publication/MMR_DiversityBased_Reranking_SIGIR_1998.pdf): áp dụng một biến thể lexical để giữ đa dạng trong tập ứng viên đã qua điều kiện bắt buộc; không tuyên bố triển khai neural reranker.
- [Preference elicitation, SIGIR 2025](https://research.google/pubs/asking-clarifying-questions-for-preference-elicitation-with-large-language-models-2/): tiếp tục dùng câu hỏi có ích cho quyết định thay vì hỏi mọi slot. Kết quả quà tặng bên dưới cho thấy prompt/reviewer hiện chưa thực hiện ổn định nguyên tắc này.
- [AAD về thử sản phẩm chăm sóc da](https://www.aad.org/public/everyday-care/skin-care-secrets/prevent-skin-problems/test-skin-care-products), [FDA về dị nguyên mỹ phẩm](https://www.fda.gov/cosmetics/cosmetic-ingredients/allergens-cosmetics), [FDA về parabens](https://www.fda.gov/cosmetics/cosmetic-ingredients/parabens-cosmetics): thêm ghi chú tham chiếu có phạm vi trong `data/retail_reference_guidance.json`. Nhãn hướng tới da nhạy cảm không chứng minh không kích ứng; tránh thành phần từng gây phản ứng; không quy mọi paraben/chất bảo quản thành độc hại.
- [ASPCA về sản phẩm gia dụng](https://www.aspca.org/pet-care/aspca-poison-control/poisonous-household-products), [EPA về bảo vệ thú cưng khi dùng sản phẩm](https://www.epa.gov/pets/read-label-first-protect-your-pets): hướng dẫn dùng theo nhãn, giữ vật nuôi tránh khu vệ sinh và chờ khô theo hướng dẫn. Tên hương hoa/tinh dầu không chứng minh an toàn.

Các ghi chú tham chiếu là kiến thức chung đưa vào cả sinh và review, không phải đặc tính đã xác minh của SKU, không phải đáp án mẫu cho 35 câu. Test riêng tránh đưa ghi chú mỹ phẩm vào câu bàn chải/răng nhạy cảm hoặc ghi chú chất tẩy vào câu mua thức ăn cho mèo. Việc model vẫn bỏ qua ghi chú phải được ghi là lỗi, không tính thành đã giải quyết nhờ thêm tài liệu.

## 3. Text thật: kết quả và lỗi còn lại

### Các lượt HTTP trước sửa truy xuất qua nhiều ngành

- `consultation_research_http_20261115.json`: 12 lượt, 9 HTTP 200, 3 HTTP 502. Vẫn có lỗi nội dung; đây không phải 9 câu đạt.
- `consultation_research_http_final_20261115.json`: 6 lượt, 3 HTTP 200, 3 HTTP 502. Lỗi quota và lỗi chặn nội dung được phân biệt bằng `assistant_busy` / `assistant_unavailable`.
- `consultation_sensitive_source_verified_20261115.json`: một trace được reviewer chấp nhận sai. Câu chọn Johnson's Baby “không cay mắt” rồi hứa giảm khó chịu cho da nhạy cảm; nhãn không chứng minh điều đó. Đó là bằng chứng về reviewer false acceptance.

### Sau sửa truy xuất qua nhiều ngành

`consultation_context_retrieval_http_20261115.json`: 6 lượt, 2 HTTP 200, 4 HTTP 502 (1 quota, 3 nội dung). Hai lượt 200 cũng chưa đạt: bánh mì “vị phô mai giúp no lâu”; tư vấn lẩu tự ra 1,5–2kg thịt khi chưa có căn cứ khẩu phần. Sau lần chạy này thêm chặn lời hứa no lâu và sửa false positive nhắc lại nhu cầu.

`consultation_context_retrieval_final_http_20261115.json`: 6 lượt, 3 HTTP 200, 3 HTTP 502 (2 quota, 1 nội dung).

| Nhu cầu | Kết quả thực tế | Đánh giá nội dung |
| --- | --- | --- |
| Ăn sáng nhanh, no | Chọn bánh mì; nói “có nhân pizza và phô mai”, “đủ năng lượng” từ tên ghi xốt/vị | Chưa đạt; vẫn suy diễn thành phần/khẩu phần và lý do chung chung |
| Quà cho mẹ 300k, chưa biết mua gì | Chọn bông tẩy trang, tự gán dưỡng ẩm | Không đạt; chưa hỏi sở thích, công dụng không được chứng minh |
| Da nhạy cảm | Hỏi từng phản ứng với thành phần nào | Tiến được hội thoại, chưa hoàn tất khuyến nghị; không coi là đã chọn đúng hàng |
| Lẩu, 3 lượt | 2 lượt hết quota và 1 lượt bị chặn vì tự đổi tiền thành khối lượng | Chưa nghiệm thu luồng |

Trace `consultation_skin_context_final_trace_20261115.json` xác nhận đã tìm thấy Dove. Tuy vậy composer 120B còn viết “công thức nhẹ/ít gây kích ứng”; reviewer chuyển sang hỏi thành phần cần tránh dù chưa có bằng chứng khách có dị ứng cụ thể. Câu hỏi cuối không phải lỗi tra catalog, nhưng chưa đạt mục tiêu chốt gọn một lựa chọn hữu ích.

Trace `consultation_meat_context_trace_20261115.json` cho thấy candidates có nhiều ba chỉ cắt lát đúng loại. Model lặp lời hứa “đủ dùng cho một bữa lẩu gia đình”, rồi hướng dẫn chung tự đưa 1–2kg nên bị chặn. Lỗi chính ở sinh/sửa lý do, không phải không có thịt trong catalog. Lần kiểm tra phản hồi có dấu (`consultation_meat_context_accented_feedback_20261115.json`) gặp quota trong vòng tiếp; chưa đủ bằng chứng khẳng định đổi feedback đã cải thiện chất lượng LLM.

Lượt runtime cuối `consultation_context_runtime_smoke_20261115.json`: 5 lượt, 2 HTTP 200 và 3 HTTP 502 do quota. Câu “loại nào đáng tiền” hỏi đúng nhóm hàng; câu so sánh chưa rõ hai món hỏi đúng tên hai sản phẩm. Cả ba lượt hội thoại da nhạy cảm gặp quota, nên chưa thể nghiệm thu khả năng giữ ngữ cảnh/chốt hàng của flow này.

## 4. STT, VAD, TTS và giới hạn kiểm tra

Không đổi provider hay ngưỡng VAD trong đợt sửa tư vấn này. Runtime dùng **Azure conversation vi-VN**, kiosk HTTP; các phép đo này không phải kiểm thử tích hợp LiveKit.

- `stt_trace_context_retrieval_20261115.json`: 6/6 mẫu tiếng Việt đúng tham chiếu. Nam/nữ nói “Em mua thịt về ăn lẩu”, có cả bản thêm nhiễu 10dB; cùng hai mẫu “Em cần nước lau sàn”. Hash audio upload khớp, dấu từ provider sang API giữ nguyên. Không tái hiện “lẩu → lấu” trên sáu mẫu này; không suy ra mọi giọng nói đều đúng.
- `voice_latency_context_retrieval_20261115.json`: hai lần STT HTTP 1.375ms và 878ms. TTS trả audio theo chunk thật, first byte 890ms ở lượt đầu và 192ms ở lượt sau. Lượt trước đo 220/229ms; không thể hứa TTS luôn dưới 250ms.
- `voice_browser_research_20261115.json`: 6/6 lần chạy với WebAudio tổng hợp, VAD browser và Azure STT/TTS thật; không gọi LLM, không có lỗi JS. Bốn lượt cùng câu có/không STT preview: từ cuối lời nói tới TTS phát 3.193/2.689ms khi tắt, 1.855/1.763ms khi bật. Đây là các mẫu đơn, không phải p95.
- Câu tự sửa “ít đường. Không đường.” giữ cả phần sửa; preview cũ bị hủy. Câu nói kèm tiếng quạt 5dB giữ “Bỏ bánh bao ra, mình muốn mua thịt tươi”.

**Chưa xác minh trong lần này:** mic/loa vật lý của laptop, echo thật khi ngắt lời, giọng người tự nhiên ở nhiều khoảng cách, nền quạt không có tiếng người, và toàn bộ độ trễ gồm LLM. Sáu fixture không chứng minh lọc ồn hoặc barge-in đã hoàn hảo. Luồng tư vấn có thể mất nhiều giây do nhiều lượt sinh/review và quota; không lấy thời gian voice không có LLM để quảng cáo tốc độ toàn hệ thống.

## 5. Regression và trạng thái bàn giao

- Backend: 890 test pass; `backend_context_retrieval_20261115.xml` lưu lần chạy. Nhóm `test_consultation_customer_review.py` có 56 ca test các lỗi đã tái hiện; model giả kiểm tra cơ chế, không chấm chất lượng model thật.
- Frontend: 66/66 test pass, production build thành công. Build vẫn có cảnh báo bundle lớn của model-viewer; không sửa ngoài phạm vi để chỉ xóa cảnh báo.
- Ruff cho các module mới và hai script voice audit: pass. `git diff --check`: không có lỗi whitespace, chỉ cảnh báo LF/CRLF của các thay đổi sẵn có.
- Backend được khởi động lại để nạp sửa; frontend demo vẫn ở `http://127.0.0.1:5173/`.

Không có nhánh đáp án riêng cho ăn lẩu, quà cho mẹ hoặc 35 câu mẫu. Không đổi model production chỉ dựa trên vài mẫu; không ghi API key vào artifact/báo cáo. [Manifest follow-up](artifacts/consultation_research_followup_manifest_20261115.json) ghi hash mã, nguồn dữ liệu và kết quả; các manifest cũ giữ theo lần đo lịch sử.

## 6. Những việc chưa đạt phải tiếp tục xử lý

1. Bảo đảm phát hiện quyết định mua hàng thiếu căn cứ, nhất là quà tặng chưa rõ sở thích; reviewer cùng nhóm model hiện vẫn chấp nhận sai.
2. Giảm các lời hứa thành phần/hiệu quả/khẩu phần do model suy diễn từ tên hàng. Không giải quyết bằng cách cứ thêm một regex hoặc mẫu đáp án cho từng câu.
3. Giữ lựa chọn có căn cứ qua vòng sửa; không để một lỗi câu chữ đẩy câu trả lời thành tư vấn chung rồi 502. Feedback có dấu đã sửa cơ chế, còn cần đối chiếu nhiều lượt khi quota cho phép.
4. Đánh giá lại cả 35 câu và flow dài với thang chấm nội dung độc lập, lưu cả thất bại. Lần này chưa có đủ lượt thành công để nghiệm thu toàn bộ.
5. Kiểm chứng voice trên mic/loa thật và tiếng quạt thực tế; không đánh đồng fixture tổng hợp với tình huống người dùng đã báo lỗi.
