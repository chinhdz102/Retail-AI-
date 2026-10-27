# Kiểm tra tư vấn theo ngữ cảnh — 27/10/2026

## Bổ sung: hội thoại tự nhiên, xếp giá và hỏi vị trí

### Phát hiện và sửa

- Tìm ra lỗi truy vấn vị trí: “Chỉ giúp mình vị trí của Thịt Thăn bò nhé” bị chuyển thành truy vấn
  chứa token thừa `vi`, nên catalog trả nhiều kết quả mơ hồ dù cụm tên hàng đã có chính xác.
  Chuẩn hóa cụm “vị trí của” trước khi tìm kiếm; không bỏ các token thuộc tên sản phẩm như
  “thăn”. Câu ngắn “thịt thăn bò ở đâu” và câu đầy đủ nay tìm cùng mã hàng.
- Lượt hỏi “bán chạy nhất” trong một nhóm trước đây có thể không giữ danh sách sản phẩm vừa
  giới thiệu. Hiện Sky giữ danh sách và giá thật làm căn cứ, nói rõ catalog không có doanh số
  được xác minh, rồi đưa các mặt hàng trong nhóm để người dùng lọc tiếp; không tự gán nhãn
  bán chạy.
- “Rẻ nhất” được tính trong tập lựa chọn đã hiển thị. Khi chưa rõ nhóm hàng, Sky hỏi nhóm nào
  cần so sánh thay vì trả kết luận rộng hoặc hỏi yes/no chung chung. Không so sánh giá gói với
  giá đơn vị khối lượng nếu không có dữ liệu quy đổi.

### Kiểm tra mới sau các sửa đổi

| Kiểm tra | Kết quả |
| --- | --- |
| Truy vấn vị trí qua HTTP, 12 sản phẩm × 6 cách hỏi tự nhiên | **72/72 đạt**; sản phẩm khớp đúng ID và vị trí khớp planogram, có ghi rõ vị trí gợi ý |
| Tư vấn catalog qua HTTP | **33/33 tình huống, 36/36 lượt đạt** |
| Hội thoại nhiều lượt qua HTTP streaming | **8 luồng, 19/19 lượt đạt** |
| Bộ hồi quy API và ngữ cảnh | **371 test đạt** sau khi thêm ca hỏi giá rẻ nhất mà chưa nêu nhóm hàng |
| Toàn bộ test backend | **1.029 passed** |
| Ruff | **All checks passed** |

Đã xem trực tiếp thêm chuỗi “Gợi ý dầu gội” → “loại nào bán chạy nhất” → “loại nào rẻ nhất”.
Sky giữ đúng ba dầu gội đã đưa ra, không nhận vơ doanh số, sau đó chọn Romano 127.600đ là giá
thấp nhất trong đúng ba lựa chọn đó. Khi bắt đầu hội thoại bằng “loại nào bán chạy nhất?” Sky
hỏi rõ nhóm hàng. Khi chỉ nói “tìm sản phẩm giá thấp nhất giúp tôi”, Sky hỏi nhóm hàng trước khi
đưa đề xuất. Dữ liệu giá trong câu trả lời khớp thẻ sản phẩm của snapshot.

Các log tái lập mới: [vị trí 36 câu](artifacts/catalog_location_final_20261027.json),
[tư vấn catalog](artifacts/catalog_advice_final_20261027.json),
[tư vấn nhiều lượt](artifacts/contextual_consultation_final_20261027.json).

### Cơ sở nghiên cứu và cách áp dụng

- [ProductAgent (EMNLP Industry 2025)](https://aclanthology.org/2025.emnlp-industry.25/) mô hình hóa việc mua hàng mơ hồ như một vòng lặp: giữ bộ nhớ hội thoại có cấu trúc, rút gọn đặc điểm ứng viên, hỏi làm rõ có mục tiêu, rồi truy xuất lại. Ở đây áp dụng bằng việc giữ nhóm hàng và tập sản phẩm/giá đã xác thực qua các lượt; hỏi nhóm khi chưa có ngữ cảnh; không để LLM tự tạo ứng viên.
- [Explicit Query Rewriting for Conversational Dense Retrieval (EMNLP 2022)](https://aclanthology.org/2022.emnlp-main.311/) nhấn mạnh chỉ sửa phần cần thiết của truy vấn phụ thuộc ngữ cảnh để tránh làm lệch ý. Sửa lỗi “vị trí của” theo hướng chuẩn hóa cụm chức năng, giữ nguyên cụm tên hàng “thịt thăn bò”.
- [When in Doubt, Ask First (Findings IJCNLP 2025)](https://aclanthology.org/2025.findings-ijcnlp.27/) đề xuất tách câu mơ hồ/không trả lời được và hỏi lại thay vì đoán, đặc biệt cho câu hỏi tiếng Việt ngắn, nhiều ngữ cảnh. Vì snapshot không có doanh số hay đánh giá vị, Sky nói rõ giới hạn nhưng vẫn trình bày các ứng viên có thật và bước tiếp theo.
- [Which Works Best for Vietnamese? (Findings EACL 2026)](https://aclanthology.org/2026.findings-eacl.110/) đánh giá IR tiếng Việt trên nhiều miền và ghi nhận truy vấn khẩu ngữ, viết tắt, lỗi chính tả, thiếu dấu, từ đệm và lệch văn phong làm giảm truy hồi. Vì vậy benchmark thêm nhiều cách hỏi tự nhiên và giữ phép truy hồi lexical có kiểm soát; chưa tuyên bố hệ thống đã bao phủ mọi phương ngữ hoặc thay BM25 bằng một mô hình embedding chưa được đánh giá trên catalog này.

### Phạm vi và giới hạn

- Các con số trên là kết quả tự động so với catalog/planogram và luồng API hiện có, không phải nghiên cứu người dùng hoặc điểm đo tổng quát cho mọi câu tiếng Việt.
- “36/36 vị trí” xác nhận đối chiếu dữ liệu planogram gợi ý; không chứng minh mặt hàng đang được bày đúng vị trí ở cửa hàng vật lý.
- Giá là snapshot tham khảo ngày 15/09/2026, tồn hiện tại chưa xác minh. Không có dữ liệu doanh số, đánh giá vị/chất lượng, hoặc đơn giá thống nhất cho so sánh quy cách khác nhau.
- Nghiên cứu được dùng để định hướng thiết kế và bộ kiểm thử; chưa tái tạo hay huấn luyện lại mô hình trong các bài báo.

## Những lỗi được xử lý

- **Giá thấp nhất:** Sau khi giới thiệu nhiều sản phẩm, Sky tra lại giá của tất cả lựa chọn vừa hiển thị và chỉ xếp hạng trong tập đó. Nếu thiếu giá, Sky không đoán. Nếu mới có một lựa chọn, Sky nói rõ và đề nghị tìm thêm. Với câu hỏi chuyển sang nhóm khác hoặc hỏi toàn catalog, Sky không dùng danh sách cũ để kết luận.
- **Bán chạy nhất, ngon nhất, đánh giá tốt nhất:** Sky giữ ngữ cảnh nhóm đang xem và phân biệt từng ý hỏi. Snapshot không có số liệu bán hoặc đánh giá vị đã xác minh, nên không khẳng định xếp hạng; Sky đề nghị lọc theo nhu cầu hoặc giá. Với hàng không phải thực phẩm, câu hỏi “ngon nhất” được xác nhận lại thay vì giả định có đánh giá vị.
- **Món ăn và cách chế biến:** Không dùng từ “ngon” trong tên làm bằng chứng rằng sản phẩm hợp để nhúng lẩu. Với các cách chế biến được nhận diện, hệ thống chỉ đưa sản phẩm nếu tên catalog có nhắc đến cách đó, và nói rõ đây chỉ là thông tin trong tên; nếu không đủ căn cứ thì hỏi thêm hoặc hướng dẫn xác minh. Mục đích được giữ khi người dùng chọn loại nguyên liệu trong cùng nhóm và được xóa khi đổi nhóm. Kiểm thử riêng phân biệt “để lau mặt” với “nấu lẩu”.
- **Thanh toán:** Câu yêu cầu như “cho t thanh toán” được đưa vào luồng giỏ hàng demo. Giỏ trống được giải thích rõ và có hướng dẫn tới quầy nếu mua trực tiếp. Đơn hàng demo vẫn cần bước xác nhận; không thực hiện thanh toán thật.

## Kết quả kiểm tra

- **19/19 lượt** trong 8 hội thoại nhiều bước đạt các điều kiện của benchmark qua `/agent/respond/stream`, chính là endpoint frontend dùng. Ví dụ: sau 3 dầu gội giá 261.000đ, 189.400đ và 127.600đ, câu “loại nào rẻ nhất” trả đúng Romano 127.600đ trong các lựa chọn đã hiển thị.
- **36/36 lượt, 33/33 tình huống** trong benchmark tư vấn catalog, có các nhóm như chăm sóc cá nhân, khăn mặt, đồ ăn và đồ dùng. Một lượt khăn mặt từng thất bại do nhầm “lau” với “lẩu”; đã sửa và chạy lại toàn bộ benchmark này.
- **45 test đạt** trong hai bộ kiểm thử tập trung vào ngữ cảnh, giá, phương ngữ, cách chế biến, giỏ hàng và tư vấn catalog sau bản sửa cuối.
- **1.015 test đạt** trong bộ `pytest tests` chạy trước chỉnh sửa phân biệt “lau/lẩu” cuối cùng; sau chỉnh sửa này đã chạy lại hai bộ kiểm thử liên quan và hai benchmark bên trên.
- Kết quả chi tiết: [hội thoại qua streaming](artifacts/contextual_consultation_sse_20261027.json) và [tư vấn catalog](artifacts/catalog_advice_recheck_20261027.json). Các tiêu chí tự động kiểm tra dữ liệu và hành vi dự kiến, không phải điểm đánh giá toàn diện độ tự nhiên hay tỷ lệ ảo giác ngoài thực tế.

## Giới hạn dữ liệu và phạm vi

### Kiểm tra mở rộng theo nhóm hàng

- Quét tên danh mục ở tầng cuối: **344/344 nhóm** trả về sản phẩm đúng phạm vi, sử dụng trực tiếp tool catalog. Trước khi sửa, nhóm “Đồ dùng nấu nướng” thất bại vì cụm “nấu nướng” trong tên nhóm bị hiểu là yêu cầu chọn thực phẩm để nướng. Hệ thống nay loại phần tên nhóm khi nhận diện yêu cầu chế biến, vẫn giữ yêu cầu chế biến riêng trong phần còn lại. [Chi tiết bộ quét](artifacts/catalog_taxonomy_sweep_20261027.json).
- **28/28 hội thoại** qua API với các nhóm thực phẩm, chăm sóc cá nhân, đồ gia dụng và các nhóm khác đạt kiểm tra tên, giá, lựa chọn rẻ nhất trong danh sách đã hiển thị và không tự khẳng định bán chạy. [Chi tiết hội thoại](artifacts/catalog_followup_sweep_20261027.json). Đây là 28 câu hỏi ban đầu, mỗi câu có hai lượt hỏi tiếp, không phải kiểm tra mọi cách diễn đạt có thể xảy ra.
- Sau sửa, **80 test liên quan đạt**; benchmark streaming nhiều lượt vẫn đạt **19/19**. Các phép kiểm tra này không xác nhận được công dụng hay chất lượng thực tế khi catalog thiếu bằng chứng.

Catalog là snapshot WinMart gồm khoảng 3.081 sản phẩm, dữ liệu ngày 15/09/2026; giá chỉ tham khảo, tồn kho hiện tại chưa xác minh. So sánh rẻ nhất không khẳng định rẻ nhất toàn cửa hàng, và giá theo gói không đồng nghĩa giá theo đơn vị khối lượng thấp nhất. Việc tên có nhắc cách chế biến cũng không xác nhận tính phù hợp, thành phần hay an toàn; ngôn ngữ về cách chế biến mới bao phủ một số cách nói. Lượt này kiểm tra văn bản và API; không đo lại chất lượng STT/TTS hoặc thanh toán thật.

Hướng tiếp cận dùng câu hỏi làm rõ và truy xuất theo ngữ cảnh được tham khảo từ [Conversational Search with Mixed-Initiative](https://aclanthology.org/2022.dialdoc-1.7/) và [Conversational Recommendation as Retrieval](https://aclanthology.org/2023.nlp4convai-1.13/). Đây là tham khảo nguyên tắc, không phải triển khai toàn bộ mô hình trong các bài báo.
