# Kiểm tra catalog cũ — 10/11/2026

Demo đang đọc file cũ `data/thanh_lam_products.json` trong worktree
`C:/Users/Chinhdz/.codex/worktrees/vietnamese-whisper-stt/Retail-AI--main`.
Đã giữ nguyên bộ này theo yêu cầu của người dùng, không nạp bộ JSON/CSV mới
có lượt bán ở thư mục `Desktop/Du an/retail`.

## Nguyên nhân đã tái hiện

Catalog không bị mất kết nối. File có 3.099 dòng; importer giữ 3.081 sản phẩm
và loại 18 dòng có giá sale lớn hơn giá gốc theo quy tắc đã có.

1. Tìm kiếm so chuỗi con rồi lấy các dòng xuất hiện đầu tiên. `OMO` khớp vào
   `Ajinomoto`; `Hảo Hảo` khớp vào `thảo` và không kiểm tra số lần lặp.
   Hỏi “Mì Hảo Hảo giá bao nhiêu?” thực tế trả về kẹo Ricola/bánh Karo.
2. Luồng công cụ dùng các nhóm `drink`, `dairy`, `meal`, trong khi snapshot
   lưu ngành hàng tiếng Việt. Ba nhóm này trả danh sách rỗng.
3. Bộ tách câu hỏi xóa nhầm từ mang nghĩa sản phẩm: “dầu ăn” thành rỗng,
   “dầu gội” thành “gội”, “thịt bò” thành “thịt”, “trà xanh” thành “xanh”.
4. Câu trả lời cuối có mã sản phẩm nhưng bỏ quên trường `products`, nên giao
   diện không nhận được thẻ sản phẩm kể cả khi đã tra đúng giá.

## Sửa đổi

- Khớp theo từ đầy đủ, kiểm tra từ lặp trong từng trường và xếp kết quả theo
  mã chính xác, thương hiệu, tên có dấu, cụm tên và độ phủ các từ trong tên.
  Giữ các bộ lọc thương hiệu, ngành hàng, mức giá, tồn ghi nhận và độ cay.
- Bước tìm trong catalog giữ các từ của mô tả sản phẩm; bỏ cụm hỏi thông tin
  thay vì xóa từng từ đơn. Ca kiểm tra thêm đã tìm ra “nước lau sàn” bị mất từ
  “sàn”, gây lẫn nước lẩu/nước lau kính; đã sửa và thêm kiểm tra phân biệt dấu.
- Nối các nhóm hàng của công cụ với phân loại của snapshot.
- Sửa tách câu hỏi để giữ các từ tên hàng đã bị xóa nhầm.
- Gửi thẻ sản phẩm đã tra được về giao diện. Không tạo giá hoặc sản phẩm mới.
- Dùng bộ nhớ đệm có giới hạn cho việc chuẩn hóa văn bản lặp lại.

Không thay file dữ liệu. SHA-256 trước/sau:
`a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`.

## Kiểm tra

| Kiểm tra | Kết quả |
| --- | --- |
| 70 ca HTTP trên demo: từ khóa nhiều ngành hàng, nhóm hàng, bộ lọc, mã/item/barcode | Trước sửa 47/70; sau sửa 70/70 |
| Toàn bộ kiểm tra backend, gồm các ca mới | 567 qua |
| Kiểm tra quy tắc mã nguồn phần thay đổi | Qua |

70 ca là kiểm tra lấy đúng dữ liệu theo các tiêu chí cụ thể; không phải tỷ lệ
đúng toàn bộ câu hỏi khách hàng hay bằng chứng tư vấn đạt trình độ nhân viên.

Đã hỏi thêm 4 câu qua LLM/công cụ thật ở bản sửa đầu: Hảo Hảo, giá bánh bao,
đồ uống dưới 20 nghìn, nước giặt OMO. Bốn lượt đều HTTP 200 và có thẻ sản phẩm
(5, 1, 3, 5 thẻ). Giá bánh bao Thọ Phát 250 g trả 30.700đ, đúng file. Câu hỏi
Hảo Hảo còn nhiều quy cách nên trả các biến thể mì phù hợp để hỏi lại, không
tự gán một mức giá cho toàn bộ thương hiệu. Các lượt mất 0,70–39,98 giây;
độ trễ LLM và cách tư vấn của bản cũ vẫn cần cải thiện.

Bản cuối đã được nạp vào cổng 8000 và chạy lại đủ 70 ca HTTP. Không sửa STT,
TTS hoặc cấu hình âm thanh trong lần sửa catalog này. Chưa kiểm tra mic vật lý.

## Bằng chứng và chạy lại

- `artifacts/catalog_old_live_before_20261110.json`: 70 ca trước sửa.
- `artifacts/catalog_old_live_after_20261110.json`: lượt kiểm tra trung gian
  (69/70 và 4 hội thoại thật), giúp phát hiện lỗi nước lau sàn.
- `artifacts/catalog_old_live_final_20261110.json`: 70/70 trên bản cuối.
- `artifacts/catalog_old_full_20261110.xml` và `.txt`: kết quả 567 bài.
- `evaluation/audit_snapshot_catalog.py`: chạy lại API bằng
  `python -m evaluation.audit_snapshot_catalog --output <file.json>`;
  thêm `--chat` để hỏi 4 câu qua nhà cung cấp thật.

Snapshot là dữ liệu đã ghi nhận ngày 15/09/2026; việc lấy đúng file không xác
nhận giá/tồn hiện tại tại một cửa hàng cụ thể. Bộ lượt bán mới đã được dừng
tích hợp theo yêu cầu mới nhất của người dùng.
