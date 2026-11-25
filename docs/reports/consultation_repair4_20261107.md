# Kiểm tra tư vấn thực tế và sửa lỗi — vòng 4, ngày 07/11/2026

**Kết luận: chưa đạt mức nhân viên thực sự; tiếp tục dùng trong demo có kiểm soát.**

Đã sửa các lỗi có bằng chứng về ngân sách, đơn vị bán, lời tư vấn rỗng, sửa nháp,
ngữ cảnh hỏi “vì sao”, dữ liệu hướng dẫn của hãng và định tuyến câu hỏi về sản phẩm.
Hỏi lại demo xác nhận hướng dẫn sử dụng/pha chế hoạt động, kể cả sau một lượt tư vấn
thất bại. Phần tư vấn chọn mua vẫn có câu lặp tên/giá, suy thêm thuộc tính và lỗi dịch
vụ. Số bài kiểm tra phần mềm đạt không chứng nhận chất lượng hội thoại.

Không tắt laptop. Demo để mở tại <http://127.0.0.1:5173/>.

## 1. Phạm vi và cách đánh giá

Tiếp nối [vòng 3](consultation_repair3_20261106.md), tập trung sửa lỗi đã xuất hiện khi
hỏi thật, kiểm tra lại bằng giao diện/API đang chạy, và giữ nguyên bằng chứng trước
sửa. Không viết câu trả lời theo SKU cho từng ví dụ của khách.

Ba loại kết quả được ghi riêng:

- **Tra thông tin:** đúng sản phẩm, đúng dữ kiện/đơn vị/phạm vi nguồn, hiểu câu tiếp.
- **Tư vấn chọn mua:** đúng nhu cầu và ràng buộc, giúp quyết định bằng lý do có ích,
  giữ ngữ cảnh, diễn đạt tự nhiên. Đúng tên/giá chưa đủ đạt.
- **Khả dụng:** một lượt bị deadline hoặc giới hạn nhà cung cấp vẫn là thất bại phục
  vụ khách; không dùng nó để suy ra riêng khả năng hiểu nghĩa của model.

Các câu đã được xem và sửa nhiều lần là dữ liệu phát triển. Những lượt này có liên
quan với nhau, không phải mẫu độc lập để tính độ chính xác hay tỷ lệ đạt toàn cửa hàng.

## 2. Lỗi đã tìm thấy và thay đổi

| Vấn đề có bằng chứng | Sửa và giới hạn |
| --- | --- |
| Giỏ ăn sáng 75.500đ vượt 60.000đ; yêu cầu sửa lại bắt giữ nguyên món | Bổ sung phép tính ngân sách độc lập: giá tối thiểu mỗi vai trò bắt buộc, trần số lượng sau khi dành tiền cho vai trò khác, tổng nháp và phần vượt. Sửa món/lượng/vai trò sai trước khi sửa lời. Phép tính không suy khẩu phần, thời gian sử dụng hay độ phù hợp. |
| Nhiều nhóm thay thế cho cùng một việc bị hiểu là đều phải mua | Chính sách phân biệt lựa chọn thay thế với giỏ mua cùng; món phụ tùy chọn không trở thành vai trò bắt buộc. |
| Nháp sai bị đưa qua bộ duyệt chỉ đọc trước khi được sửa | Luồng Groq gốc dùng lỗi kiểm tra tại chỗ làm phản hồi cho một lần sửa có giới hạn; chỉ duyệt ngữ nghĩa bản đã kết xuất thành công. Lần sửa tiếp vẫn sai thì không phát nháp đó. Tích hợp biên tập cũ giữ hợp đồng riêng. |
| Tên hàng ghi túi 2kg nhưng đơn vị nhập là “Gói”, khiến lọc túi loại bỏ món đúng | Ưu tiên đơn vị túi ghi rõ kèm số đo trong tên, có đối chứng ở nhiều nhóm hàng; bao ngoài/lốc vẫn được giữ. Không coi mọi gói, túi, chai là tương đương. |
| Selector trả `text=[]`, ứng dụng biến thành lời liệt kê hàng chung chung | Từ chối lựa chọn có lời giải thích rỗng. Schema native yêu cầu ít nhất một block khi có ứng viên và explanation có nội dung. Câu hỏi chưa chọn món vẫn được phép. |
| Bộ duyệt đặt cờ hội thoại false nhưng không viết issue, làm luồng bị abort | Giữ kết quả từ chối và dùng tên cờ thất bại làm phản hồi sửa; không biến false thành chấp thuận. Lỗi kiểm tra tại chỗ cũng không bị model ghi đè bằng approval. |
| “Tư vấn vì sao nên chọn loại này” lập kế hoạch lại dù món đã hiện | Nhận dạng một nhóm ngữ pháp hỏi lý do có phạm vi đóng, tái sử dụng phạm vi đã được chấp nhận. Tên hàng, ngân sách hoặc thuộc tính mới vẫn phải được hiểu lại. |
| Bốn hồ sơ bổ sung của hãng đồng loạt mất hiệu lực | Đối chiếu đủ ID/tên/hãng/barcode của cả bốn sản phẩm rồi cập nhật liên kết hash catalog. Không sửa nội dung dữ kiện, thời điểm quan sát/xác minh, hay nâng tuyên bố quảng cáo thành dữ kiện đã kiểm chứng. |
| Khi bật tư vấn, hỏi P/S dùng thế nào, Hảo Hảo pha thế nào hoặc Pantene có công dụng gì lại rơi vào fallback | Câu hỏi dữ kiện về sản phẩm cụ thể đi tới bộ điều khiển kiến thức hiện có trước khi gọi planner. Định danh và biến thể mơ hồ vẫn do bộ đó giải quyết. Yêu cầu chọn hàng, da nhạy cảm, hạn chế đường, ngân sách và hỏi lý do chọn vẫn đi qua tư vấn. |
| Lời trả đọc `Mục nguồn: ...energy_kcal`, nói về catalog và ghép dấu `.;` | Giữ đường dẫn nguồn trong quan sát nội bộ, bỏ khỏi lời nói; sửa ghép câu hướng dẫn. Vẫn giữ đơn vị tính và giới hạn nguồn khi chúng ảnh hưởng quyết định, nhất là số caffeine của lon không được coi là số của chai khác. |
| Demo suy từ nhãn sữa “không đường” thành “không thêm đường, chỉ còn lactose” | Bổ sung tài liệu FDA về đường tổng số/đường thêm vào; kiểm tra lời phủ định phân biệt hai loại khẳng định này. Mệnh đề độc quyền thành phần như “chỉ chứa…” cần lời nguồn tương ứng. Sửa tách mệnh đề để “chỉ còn” không bị hiểu là liên từ đối lập. Đây là kiểm tra ngôn ngữ có phạm vi hẹp, chưa phải bộ suy diễn thành phần tổng quát. |

### Liên kết dữ liệu của hãng

Catalog hiện tại có SHA256:
`992e87a5929f3a86460fb3440ea3a25e3fab6a37f62de8b371a385abe4af3ef5`.
Registry trước đó liên kết tới một hash khác, nên kiểm tra phiên bản chủ động trả
UNKNOWN. Sau khi kiểm tra bốn danh tính, chỉ trường liên kết catalog được cập nhật.
Hash chuẩn hóa phần records giữ nguyên:
`a86e62b13a955413cc956cbc9f1428b2b4b8a7a65f4045d1a2246e3623dcc84b`.

Đây là sửa liên kết dữ liệu đã có, không phải xác minh lại công dụng hay làm mới
thời điểm thu thập. Pantene vẫn là mô tả của hãng ở cấp dòng sản phẩm.
Chi tiết: [audit đối chiếu](artifacts/repair4_manufacturer_rebind_20261107.json).

## 3. Kết quả hỏi thật

### Hội thoại trên giao diện

| Giai đoạn | Câu hỏi/tình huống | Quan sát và đánh giá thủ công |
| --- | --- | --- |
| Sau sửa túi/schema, trước sửa định tuyến | Có chai sẵn, mua túi nước rửa chén dưới 60.000đ | Chọn đúng túi Sunlight 2kg, 53.000đ. Hữu ích ở mức cơ bản trong tình huống này. |
| Cùng phiên | “Vì sao nên chọn loại này?” | Giữ đúng món/ngân sách nhưng lặp lại tên, giá, dạng túi; câu chữ cứng. **Yếu**, chưa đạt nhân viên. |
| Cùng phiên | Ăn sáng hai người, 60.000đ, chỉ có ấm nước | Fallback, không giúp chọn được. |
| Cùng phiên | Đồ uống hạn chế đường dưới 20.000đ | Fallback; có 429 trong giai đoạn này. Không quy mọi lỗi cho hiểu nghĩa. |
| Sau bổ sung nguồn FDA, trước sửa kiểm tra thành phần | Lặp lại câu chọn đồ uống | Chọn sữa túi 180ml, 6.000đ nhưng tự diễn giải nhãn thành không thêm đường và “chỉ còn lactose”. **Không đạt về bằng chứng**, dù giá đúng. Giữ nguyên câu lỗi để kiểm tra hồi quy. |
| Cùng phiên | P/S 230g dùng thế nào? | Trả đúng hướng dẫn hãng, bỏ mã nguồn nội bộ và dấu ghép lỗi. Đạt tra thông tin trong lượt này. |
| Mã cuối, sau sửa kiểm tra thành phần | Lặp lại câu chọn đồ uống | Fallback; log cùng lượt có RateLimitError/BadRequestError và deadline. **Thất bại dịch vụ**; chưa chứng minh lời tư vấn sau sửa đã đạt. Không gửi thêm chuỗi câu generative dồn dập để lách giới hạn. |
| Cùng phiên mã cuối, ngay sau fallback | Hảo Hảo 75g pha thế nào? | Trả hướng dẫn khoảng 400ml nước sôi, đậy 3 phút; đúng phạm vi nguồn. Chứng minh câu dữ kiện thoát được luồng tư vấn hỏng ngay trên UI. |

Bằng chứng giữ riêng từng giai đoạn:

- [4 lượt đầu, toàn bộ nội dung](artifacts/repair4_final_ui_20261107.txt) và [ảnh](artifacts/repair4_final_ui_20261107.png).
- [Lỗi diễn giải nhãn và P/S](artifacts/repair4_label_ui_before_boundary_20261107.txt), [ảnh](artifacts/repair4_label_ui_before_boundary_20261107.png).
- [2 lượt mã cuối](artifacts/repair4_delivery_ui_20261107.txt), [ảnh](artifacts/repair4_delivery_ui_20261107.png).

Không cộng các lượt ở nhiều phiên bản thành một tỷ lệ nghiệm thu.

### API đang chạy và trace model

Trước sửa định tuyến, bốn câu về Coca-Cola Zero, P/S, Hảo Hảo và Pantene có một câu
trả được dữ kiện và ba fallback. Sau sửa, **cả bốn câu và một câu tiếp “loại này dùng
thế nào?” trả được dữ kiện**, đi qua endpoint streaming thật, trong một cuộc hội
thoại. Năm thời gian quan sát là 1,893 / 0,829 / 0,245 / 0,534 / 0,581 giây. Đây là
thời gian của lượt tra dữ kiện, không phải tốc độ tư vấn của LLM.

- [Trước sửa](artifacts/repair4_knowledge_api_20261107.json).
- [Sau sửa, 5 lượt đầy đủ](artifacts/repair4_knowledge_api_after_20261107.json).

Artifact sau sửa được chụp trước lần chỉnh dấu câu cuối; UI mã cuối xác nhận phần
dấu câu đã được chỉnh. Không ghi đè baseline.

Một trace generative độc lập trước sửa thành phần trả sữa 6.000đ sau các lần
429/400, mất 16,887 giây. Câu trả còn công thức, suy thuận tiện mang túi từ dung tích
và không phân biệt rõ nhãn với hàm lượng. Chấm **yếu**, không phải chứng nhận chọn đồ
ít đường. [Trace đầy đủ](artifacts/repair4_low_sugar_final_trace_20261107.json) mang tên
“final” theo thời điểm chạy, nhưng nó **có trước** sửa kiểm tra thành phần cuối cùng.

Những lỗi 400 quan sát được không chứng minh Qwen không hỗ trợ structured outputs.
Không có đủ bằng chứng để kết luận đã hết hạn mức theo ngày. 429 là bằng chứng giới
hạn dịch vụ ở các lần gọi cụ thể; deadline có thể làm mất trace của tác vụ bị hủy.

### Lỗi trong công cụ đánh giá cũng được sửa

Harness trace cũ xóa selected_product_ids khi một lượt fallback/không có sản phẩm,
trong khi API thực tế giữ trang hàng đã hiện. Hai trace hỏi “vì sao” cũ vì thế có
ngữ cảnh phát lại thiếu; không thể quy toàn bộ thất bại trong đó cho runtime.

Harness mới chỉ thay danh sách khi có trang hàng mới, lưu initial_state/final_state,
đánh dấu `state_replay_version=2` và khôi phục được qua nhiều lần resume. Artifact cũ
không có final_state có thể thiếu lịch sử tổ tiên nếu bản thân nó đã được resume.
Manifest các trace mới thêm cả catalog, registry và kho hướng dẫn chung.

## 4. Kiểm tra phần mềm và thí nghiệm có kiểm soát

| Kiểm tra | Kết quả / phạm vi |
| --- | --- |
| Toàn bộ backend, lần đầu của vòng này | 11 lỗi, 2.833 đạt, 1 bỏ qua; cả 11 liên quan registry không khớp hash catalog. Giữ [kết quả lỗi](artifacts/backend_repair4_20261107.xml). |
| Toàn bộ backend sau sửa liên kết registry | **2.844 đạt, 1 bỏ qua**, 380,48 giây. [XML](artifacts/backend_repair4_final_20261107.xml). Chưa gồm sửa định tuyến cuối. |
| API/định tuyến và biên lỗi tư vấn | **459 đạt** ở phiên bản định tuyến đầu. [XML](artifacts/repair4_knowledge_routing_verified_20261107.xml). |
| Định tuyến có bật consultation, REST và SSE, phiên mới/có yêu cầu treo, nhiều nhóm hàng và đối chứng chọn mua | **25 đạt** sau bổ sung cách hỏi hướng dẫn. Model test buộc không được gọi ở lượt tra dữ kiện. [XML](artifacts/repair4_knowledge_routing_final_20261107.xml). |
| Định tuyến, nguồn chung và kiểm tra bằng chứng sau chỉnh dấu câu | **61 đạt**. [XML](artifacts/repair4_evidence_guidance_final_20261107.xml). |
| Toàn bộ backend sau định tuyến/nguồn chung, trước kiểm tra thành phần cuối | **2.870 đạt, 1 bỏ qua**, 643,95 giây. [XML](artifacts/backend_repair4_delivery_20261107.xml). |
| Kiểm tra thành phần, bound text, verifier và sửa native | **145 đạt**; sau thêm hai đối chứng model cho qua sai, riêng bộ physical/absence có **74 đạt**. [145](artifacts/repair4_composition_boundary_final_20261107.xml), [74](artifacts/repair4_composition_delivery_20261107.xml). |
| Kiểm tra liên quan trên mã giao cuối | **1.402 đạt, 1.484 không thuộc phạm vi lọc**, 186,30 giây; không lỗi, không bài bỏ qua trong tập được chạy. Ruff đạt, hash mã/nguồn trước và sau giống nhau. [Manifest kiểm tra](artifacts/repair4_final_source_checks_20261107.json), [XML](artifacts/repair4_final_source_scoped_20261107.xml), [log](artifacts/repair4_final_source_scoped_20261107.txt). |

Một bài bỏ qua ở bộ đầy đủ do Windows không có quyền tạo symlink; một cảnh báo là
Starlette TestClient/httpx deprecation. Không có thay đổi frontend trong vòng này.
Scoped Ruff cho các file thay đổi đã đạt; manifest cuối lưu lại lần chạy cùng mã.

[Manifest bàn giao](artifacts/repair4_delivery_manifest_20261107.json) lưu hash mã,
dữ liệu và các báo cáo/bằng chứng được liệt kê, cùng trạng thái HTTP của demo. Không
có khóa API hoặc nội dung cấu hình bí mật trong manifest.

Các thí nghiệm policy rút gọn và reasoning `none` vẫn là tùy chọn đo thử, chưa đưa
vào mặc định. Một planner đông cứng dùng 300 completion tokens ở `none`, so với 1.060
ở `low`; một selector ở `low` lỗi 400, còn bản `none` vẫn yếu về nội dung. Các mẫu rất
nhỏ và khác giai đoạn không đủ kết luận `none` tốt hơn, nhanh hơn trong vận hành hay
giải quyết chất lượng. Mặc định reasoning vẫn `low`.

## 5. Nghiên cứu đã dùng và điều thực sự áp dụng

| Nguồn | Đọc và ứng dụng có giới hạn |
| --- | --- |
| [LLM-Modulo](https://arxiv.org/html/2411.14484v1) | Tách kiểm tra ràng buộc khỏi model và phản hồi lỗi có thể hành động. Áp dụng phép tính tiền/vai trò và sửa nháp có giới hạn; không tuyên bố tái hiện toàn bộ kiến trúc hay có bộ chứng minh ngữ nghĩa. |
| [Beyond expert users / CoShop, CoPref](https://arxiv.org/html/2606.30863v1) | Đọc phần giới thiệu và phân tích 4.1–4.4: hỏi thêm/search thêm chưa chắc giúp khách hình thành lựa chọn. Dùng để yêu cầu ví dụ và giải thích đánh đổi thật. Bài có môi trường mô phỏng/phạm vi riêng; số liệu của bài không phải số liệu dự án. |
| [TRACE](https://arxiv.org/html/2608.10176v1) | Đọc phần 3.1–3.3, dùng ý tưởng tách ràng buộc cấu trúc và kiểm tra theo bằng chứng. Benchmark danh bạ dịch vụ công tổng hợp khác siêu thị Việt Nam; không suy độ chính xác chuyển miền. |
| [ChatCRS, NAACL 2025](https://aclanthology.org/2025.findings-naacl.17/) | Đọc abstract, tham khảo việc tách hiểu hội thoại với kiến thức ngoài. Không dùng phần abstract để khẳng định một bộ luật hiện tại đã đạt hội thoại tự nhiên. |
| [FDA: Added Sugars on the Nutrition Facts Label](https://www.fda.gov/food/nutrition-facts-label/added-sugars-nutrition-facts-label) | Đọc phần phân biệt đường tổng số và đường thêm vào. Đường tự nhiên trong sữa/trái cây thuộc đường tổng số. Đưa tóm tắt vào kho hướng dẫn chung; không cấp thành phần, số gram, hay chứng nhận chế độ ăn cho từng SKU. |

Nguồn là đầu vào thiết kế. Bằng chứng hữu ích của dự án vẫn là câu trả quan sát
được, kiểm tra giá/vai trò, phản ví dụ và chấm hội thoại; không phải việc đã trích
nhiều paper hoặc model tự chấm mình đạt.

## 6. STT, VAD, TTS và LiveKit

Vòng này không thay cấu hình nhận dạng hoặc xử lý âm thanh. STT đang cấu hình
`whisper-large-v3`, dự phòng `whisper-large-v3-turbo`, tiếng Việt. Script demo bật xác
minh tiếng Việt cục bộ nếu có model cache phù hợp. Chat vẫn Qwen 27B với các model
dự phòng đã cấu hình; không lẫn tên model chat với model STT.

Kết quả replay tiếng nói gần nhất vẫn thuộc [vòng 3](consultation_repair3_20261106.md):
14/14 HTTP200, năm mẫu không phải lời nói đều rỗng, nhưng còn lỗi vùng miền/tên hãng.
Các bài hồi quy phần mềm trong vòng này không phải phép đo nhận dạng mới.

**Chưa có bằng chứng mới cho micro vật lý trong phòng có quạt/điều hòa, tiếng loa
vọng vào mic, mất đầu/cuối câu, ngắt lời khi TTS phát, hay kết nối LiveKit trọn luồng.**
Không ghi chúng là “ngon” hoặc đã sửa xong. Tiếp tục dùng báo cáo
[lọc ồn](noise_filtering_20261103.md) và [ý định/ASR](vietnamese_intent_asr_research_20261105.md)
làm baseline; không bật bộ lọc ồn mới chỉ vì tên kỹ thuật mạnh hơn.

## 7. Việc còn thiếu và kế hoạch nghiệm thu

1. **Khả dụng tư vấn:** giải quyết 400/429/deadline bằng đo riêng từng stage, giảm
   đầu vào trùng, sửa hợp đồng schema khi có lỗi tái hiện được và giữ backoff. Không
   đổi model mặc định chỉ dựa trên một mẫu, không restart để né cooldown, không tính
   HTTP200 chứa fallback là thành công tư vấn.
2. **Lý do mua có ích:** thử lại các câu đã lỗi (ấm nước/ngân sách, ít đường, hỏi
   “vì sao”) sau khi dịch vụ cho phép; sau đó thêm câu chưa dùng để chỉnh prompt ở
   các nhóm thực phẩm, gia dụng, vệ sinh, chăm sóc cá nhân. Chấm đúng mục đích,
   ràng buộc, ngữ cảnh và tính tự nhiên riêng. Câu chỉ lặp tên/giá không đạt.
3. **Dữ liệu và phạm vi bằng chứng:** mở rộng nguồn hãng theo danh tính/biến thể có
   kiểm tra, phân biệt số liệu đo, nhãn, mô tả hãng và kiến thức chung. Không bịa
   thành phần để tránh câu “chưa thấy trong catalog”; khi chưa chốt SKU vẫn cần lời
   khuyên chọn mua thực tế, gọn, có giới hạn đúng chỗ.
4. **Hội thoại nhiều lượt:** thay ngân sách, loại trừ, đổi ngành hàng, chọn một món,
   hỏi so sánh/lý do/cách dùng, và tiếp tục sau lỗi. Giữ bộ 35 câu của khách là bộ
   phát triển; bổ sung câu độc lập trước nghiệm thu, lưu nguyên lời trả và nguồn.
5. **Giọng nói tiếng Việt:** đo WER/CER và lỗi ý định/tên/số tiền riêng trên mẫu người
   thật; đo kích hoạt nhầm với quạt/điều hòa, mất đầu/cuối câu, thời gian kết thúc
   lượt và ngắt TTS/echo. Kiểm tra STT độc lập rồi ghép VAD–STT–tư vấn–TTS–LiveKit,
   gồm reconnect và hủy lượt. File replay không thay cho đo micro/loa thật.

Chỉ nâng kết luận khi có câu tư vấn quan sát được vừa giúp quyết định vừa đúng
ràng buộc/bằng chứng, và luồng tiếng nói được đo đúng môi trường. Vòng này sửa được
nhiều lỗi cụ thể nhưng **chưa hoàn thành nghiệm thu “như nhân viên thật”**.
