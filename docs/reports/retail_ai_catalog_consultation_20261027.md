# Kiểm tra và sửa tư vấn theo nhu cầu từ catalog — 27/10/2026

## Tình huống thực tế

Khách nói: “dạo này đầu tao nhiều gàu quá m giới thiệu giúp t vài loại dầu gội đầu”.
API cũ trả “Mình chưa tìm thấy món đó trong danh mục hiện có”, dù snapshot có dầu gội Selsun,
Clear và nhiều mặt hàng liên quan. Đã tái hiện lỗi bằng chính câu này trước khi sửa.

## Nguyên nhân và thay đổi

1. **Nhận nhầm một phần nhãn hàng.** Bộ tìm kiếm giữ từ `tao` từ TAO KAE NOI, `m` từ M&M,
   `minh` từ MINH HAO như điều kiện tên hàng. Đã chuyển sang nhận cả cụm nhãn hàng, có biên từ;
   không suy ra nhãn hàng từ một từ rời của tên nhiều từ.
2. **Intent và đường đi hội thoại.** “Giới thiệu” chưa được nhận là yêu cầu tư vấn. Câu bổ sung
   ngân sách có thể chạy sang tìm sản phẩm mới, làm mất loại hàng và nhu cầu đang tư vấn.
   Đã bổ sung cách nói tư vấn chung và ưu tiên xử lý tiêu chí trong ngữ cảnh hiện tại.
3. **Tách loại hàng khỏi lời kể.** Cụm loại hàng được lấy từ nhãn và tiền tố nhãn trong taxonomy
   đang hoạt động. Giữ nguyên trật tự cụm như “kem dưỡng da mặt”, thay vì ghép các từ lặp từ
   toàn câu thành một truy vấn sai. Danh mục cha dùng để duyệt nhóm con; không nhầm thành
   tiền tố ngắn của một mặt hàng con. Nhãn chung như “khác” không tự đổi chủ đề.
4. **Tiếng Việt và tên nhãn hàng.** Không dịch các từ tiếng Anh bên trong câu tiếng Việt một
   cách máy móc: “cần/cân” không phải `can`, và `Men` trong nhãn hàng phải được giữ nguyên.
   Với nhãn một từ, ưu tiên chữ có dấu để tránh đồng âm như “dầu” và “đậu”.
5. **Tư vấn theo nhu cầu.** Loại hàng, nhãn, kích thước, ngân sách và điều kiện tồn kho được
   giữ làm bộ lọc. Phần mô tả nhu cầu được dùng để tìm thêm và xếp hạng trong phạm vi đó.
   Có tối đa ba lượt tìm bổ sung; không nới sang nhóm khác khi không có kết quả.
   Ví dụ, từ “gàu” ưu tiên các tên dầu gội có từ này, thay vì buộc tên hàng chứa cả câu kể.
6. **Từ chối nhãn và đổi nhóm.** “Không lấy …” được áp dụng cho nhãn có thật trong catalog.
   “Hãng khác” là yêu cầu lựa chọn, không phải tên nhãn. Ngữ cảnh nhu cầu được giữ khi lọc
   tiếp; khi chuyển loại hàng, phần nhu cầu cũ không tham gia xếp hạng loại mới.
7. **Không biến tên hàng thành lời cam kết.** Kết quả gắn bằng chứng `PRODUCT_TITLE_ONLY`.
   Chỉ nêu đúng tên và giá từ snapshot; không suy ra chữa bệnh, hiệu quả, độ an toàn hay mức
   phù hợp riêng cho khách. Giá là giá tham khảo và tồn hiện tại chưa được xác minh.
8. **Không mất mặt hàng chỉ vì quy cách đóng gói.** Điểm ưu tiên chai/lon lẻ không được làm
   biến mất một kết quả khớp nếu danh mục chỉ có lốc hoặc thùng.
9. **Thời gian xử lý.** Cache có giới hạn cho chuẩn hóa và đối chiếu taxonomy, với khóa gồm
   cả taxonomy; loại bỏ việc biên dịch hàng nghìn regex lặp lại cho cùng nhãn. Tra cứu tư vấn
   có ngân sách thời gian riêng và không tự chạy lại tác vụ CPU còn đang xử lý.

Các thay đổi lấy nhãn, tên hàng và nhãn hiệu từ nguồn catalog. Không có bảng câu trả lời hay
danh sách ID dành riêng cho dầu gội hoặc cho câu khách đã nói.

## Kiểm tra

| Kiểm tra | Phạm vi | Kết quả |
| --- | --- | --- |
| Toàn bộ test backend | Hồi quy API, tool, catalog, hội thoại, giỏ hàng, mission, bảo vệ dữ liệu và voice dạng mock | **991 passed**, 177,02 giây |
| Quét taxonomy đang hoạt động | 344 tên nhóm con trong snapshot, kiểm tra kết quả nằm đúng nhánh | **344/344 đạt** |
| Hội thoại thực tế bằng text | 33 kịch bản, 36 lượt hỏi đáp; gồm khẩu ngữ, không dấu, nhu cầu, ngân sách, đổi hãng, đổi nhóm, dữ liệu thiếu và injection | **36/36 lượt đạt** |
| Kiểm tra mã | Ruff trên các file thay đổi trong đợt sửa | **All checks passed** |

Bộ hội thoại cuối trả về 74 ID sản phẩm khác nhau; tên và giá của các thẻ được đối chiếu với
snapshot. Không phát hiện sai phạm vi nhóm, vượt ngân sách đã đặt, trả hãng bị loại trong case
kiểm tra, hay cam kết hiệu quả bị cấm bởi bộ kiểm tra. Các câu tư vấn rõ nhóm đi trực tiếp qua
tool catalog; kết quả này không phải benchmark độ hiểu của mọi prompt gửi đến LLM.

Một số chuỗi đã xem lại:

| Hội thoại | Kết quả kiểm tra |
| --- | --- |
| Câu “đầu tao nhiều gàu…” trong ảnh | Trả các tên dầu gội có chữ gàu: Selsun 100ml, Clear 630g, Clear Men Cool Sport 630g; đúng giá tham khảo |
| Nối tiếp “dưới 150k” | Giữ nhóm dầu gội và nhu cầu trước, còn Selsun 100ml giá tham khảo 95.200đ |
| Nối tiếp “không lấy Clear, muốn hãng khác” | Giữ mức giá và nhu cầu, không trả Clear |
| Chuyển sang “giới thiệu khăn mặt” | Trả khăn mặt từ catalog, không kéo nhu cầu về tóc sang nhóm mới |
| Chăm sóc da, thịt, vệ sinh nhà cửa | Trình bày các loại con khác nhau kèm tên và giá; hỏi khách muốn xem nhóm nào |
| Snack vị phô mai | Giữ phạm vi snack, không chuyển sang sữa chua/phô mai thuộc nhóm sữa |
| Câu hỏi chữa khỏi gàu vĩnh viễn | Không cam kết chữa khỏi; giới hạn bằng chứng vào tên hàng và thông tin cần xác minh trên nhãn |
| Nhãn hàng không tồn tại, nhóm hàng vắng trong catalog | Không tạo ID hay giá mới; hỏi điều chỉnh tiêu chí khi thích hợp |

Đã kiểm tra lại câu gốc trên giao diện tại `http://127.0.0.1:5174/`: có câu trả lời và thẻ
sản phẩm, giá tham khảo, ngày snapshot; không tự thêm vào giỏ.
[Ảnh chụp lần kiểm tra giao diện](artifacts/catalog_advice_demo_20261027.png).

Hai kiểm tra cũ được cập nhật theo ý nghĩa nghiệp vụ: giấy khô có thể mang tên “giấy lụa rút”
hoặc “khăn rút”, nên đối chiếu nhóm và loại trừ khăn ướt; danh sách thay thế được phép có ít
hơn ba lựa chọn thật, không điền thêm mặt hàng để đủ số lượng.

Log có đầy đủ request/response và dữ liệu đối chiếu:

- [Hội thoại qua HTTP demo](artifacts/catalog_advice_20261027_verified_http.json).
- [Hội thoại chạy trong tiến trình API](artifacts/catalog_advice_20261027_verified_final.json).
- [Quét taxonomy 344 nhóm](artifacts/catalog_taxonomy_sweep_20261027_verified.json).

Lệnh tái lập:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --tb=short
.\.venv\Scripts\python.exe -m evaluation.catalog_advice_benchmark --base-url http://127.0.0.1:8000 --concurrency 3 --output docs/reports/artifacts/catalog_advice_20261027_verified_http.json
.\.venv\Scripts\python.exe -m evaluation.catalog_advice_benchmark --taxonomy-sweep --output docs/reports/artifacts/catalog_taxonomy_sweep_20261027_verified.json
```

## Ý nghĩa và giới hạn của số đo

- Bộ hội thoại thực tế giữ đầy đủ câu hỏi, câu trả lời, ID mặt hàng, tool và thời gian phản hồi
  để kiểm tra lại; kiểm tra bằng text qua API mà giao diện sử dụng.
- Quét taxonomy kiểm tra khả năng tìm hàng khi khách nêu nhãn nhóm. Đây là kiểm tra truy hồi,
  không phải đánh giá mọi cách diễn đạt tự do. Với nhãn vừa là cha vừa là lá, chấp nhận các
  nhóm con thật của nhãn cha đó, không chấp nhận hàng ngoài nhánh.
- Test tự động đối chiếu phạm vi nhóm, từ đặc điểm trong tên, giá, giới hạn ngân sách, nhãn bị
  loại và ID trong snapshot. Đây không phải phép đo đầy đủ mọi kiểu ảo giác hay đánh giá
  độ tự nhiên của mọi hội thoại.
- Snapshot không có đầy đủ dữ liệu công dụng, hiệu quả, tương thích da/tóc, dị ứng, lượt bán
  và tồn kho trực tiếp. Một tên hàng có ghi đặc điểm không chứng minh hiệu quả cho cá nhân.
- Không đo lại micro, STT hay TTS trong đợt sửa luồng tư vấn bằng text này.

## Nghiên cứu tham khảo

- [Conversational Recommendation as Retrieval: A Simple, Strong Baseline (2023)](https://aclanthology.org/2023.nlp4convai-1.13/):
  xem tư vấn hội thoại như một bài toán truy hồi có sử dụng ngữ cảnh. Thiết kế ở đây áp dụng
  nguyên tắc tìm trong catalog rồi mới trình bày kết quả; không triển khai lại toàn bộ phương
  pháp hay mô hình huấn luyện của bài báo.
- [Soliciting User Preferences in Conversational Recommender Systems via Usage-related Questions (2021)](https://research.google/pubs/soliciting-user-preferences-in-conversational-recommender-systems-via-usage-related-questions/):
  khai thác nhu cầu sử dụng để làm rõ lựa chọn. Trong sản phẩm này, khi thiếu bằng chứng về
  độ phù hợp, phản hồi cần hỏi thêm hoặc đề nghị kiểm tra nhãn thay vì tự khẳng định.
- [Corrective Retrieval Augmented Generation (2024)](https://arxiv.org/abs/2401.15884):
  nhấn mạnh kiểm tra chất lượng truy hồi trước khi sinh câu trả lời. Ở đây dùng đối chiếu
  nhóm hàng và bằng chứng tên hàng để kiểm soát kết quả; không dùng tìm web để bổ sung giá
  hay thông tin sản phẩm ngoài cơ sở dữ liệu.
