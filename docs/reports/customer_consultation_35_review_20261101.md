# Rà soát 35 câu khách hàng và chất lượng tư vấn

Ngày: 01/11/2026. Phạm vi: demo Sky/Retail trên catalog 3.081 sản phẩm; 346 đường dẫn nhóm hàng.

## Vấn đề cần sửa

Các ví dụ của người dùng là dấu hiệu của lỗi chung, không phải danh sách để viết sẵn đáp án. Bản trước có thể lấy một từ trong câu rồi tìm cả catalog: tư vấn đồ dọn nhà bằng thực phẩm, hỏi sữa lại đưa mỹ phẩm, hạn chế đường lại đưa nước ngọt thường. Khi thiếu dữ liệu về đặc tính, một số nhánh trả nguyên câu “Mình chưa có dữ liệu đã xác minh…” dù khách chỉ muốn được hướng dẫn mua hàng.

35 câu nguyên văn được giữ ở `evaluation/cases/customer_consultation_35_dev_20261031.json`. Đây là dữ liệu phát triển do người dùng cung cấp và đã được xem trong quá trình sửa, không phải tập mù. Bản trước sửa: `artifacts/customer_consultation_35_before_20261031.json`.

## Thay đổi đã triển khai

- Thêm bước chọn hành động hội thoại trước các nhánh tìm kiếm cũ: hỏi làm rõ, giới thiệu, so sánh, chỉ vị trí hoặc chuyển về bộ điều khiển nghiệp vụ.
- Chọn facet liên quan từ taxonomy thật bằng trọng số từ vựng theo catalog; đối chiếu tên nhóm trước khi tìm. Không có bảng câu hỏi → SKU, không có câu trả lời dành riêng cho 35 câu.
- Giữ nhu cầu, câu hỏi đang chờ, ràng buộc và ID hàng đã hiển thị trong phiên. Không coi “hôm trước em mua” là lịch sử mua hàng mà hệ thống đã biết.
- Tách lời gợi ý mua sắm chung khỏi thông tin về SKU. Giá, liên kết sản phẩm, quy cách được lọc, số gói và tổng được ứng dụng kiểm tra; không mặc định số gói là khẩu phần.
- Thêm quan sát sản phẩm cho bước viết câu trả lời, với tên/giá/nhóm/hãng/đơn vị. Không đưa mô tả HTML tự do thành chỉ dẫn.
- Không tìm toàn cửa hàng để lấp chỗ trống khi chưa biết loại hàng hoặc sản phẩm gốc. Thiếu tiêu chí da nhạy cảm, an toàn quanh mèo hoặc mùi thì cần hỏi đúng việc và kiểm tra nhãn.
- Chỉ đường bằng trường kệ có nguồn; sơ đồ gợi ý được ghi rõ là demo, không tự sinh “bên trái”, “gần cửa”.
- Kiểm tra định dạng và một số nhóm khẳng định thiếu bằng chứng; thử viết lại một lần. Nếu lựa chọn hợp lệ nhưng phần văn vẫn sai, giữ tên/giá có căn cứ. Nếu dịch vụ lỗi thì không thay bằng hàng ngẫu nhiên.
- Giới hạn số facet và ứng viên để tránh quá giới hạn đầu vào của dịch vụ mô hình. Đã bỏ tên mô hình/nhãn kỹ thuật khỏi bong bóng chat; metadata vẫn ở API để truy vết.
- Sửa lỗi lọc sau trang đầu: repository có thể giới hạn 20 món/trang dù caller yêu cầu 80. Luồng mới đọc phân trang trong đúng nhóm, tối đa 200 dòng mỗi nhu cầu, rồi mới lọc nhãn/quy cách và rút gọn tập ứng viên. Từ “không đường” được đưa vào bộ lọc thuộc tính, không để parser tìm kiếm hiểu nhầm là từ khóa định danh.

Nguồn nghiên cứu và đối chiếu thiết kế: [ghi chú nghiên cứu](customer_consultation_research_20261101.md), gồm ProductAgent/PROCLARE, PEPPER và survey CRS của Jannach cùng cộng sự.

## Cách đọc kết quả

1. Cả 35 câu được gửi qua HTTP vào phiên mới. “Cái này”, “hai loại này”, “loại 1 lít” chưa có món gốc phải hỏi làm rõ; trả ngay một SKU trong trường hợp này là đoán.
2. Chạy thêm các hội thoại có lượt trước để kiểm tra việc giữ nhóm hàng và sản phẩm tham chiếu. Có câu diễn đạt mới về văn phòng phẩm, pin, khăn và chăm sóc thú cưng.
3. Đọc cả nội dung, sản phẩm trả về và diễn biến. HTTP 200 hoặc test code qua không đồng nghĩa lời tư vấn tốt.
4. Tách lỗi dịch vụ, lỗi lọc dữ kiện, lựa chọn không sát, lời nói rập khuôn và khẳng định thiếu bằng chứng. Không cộng chúng thành một điểm “giống người” tự động.

Các lượt thử trung gian được giữ trong `artifacts/customer_consultation_35_*`; không dùng các bản trước khi sửa hết định dạng/phân trang làm kết quả bản cuối. Lượt đầy đủ gần nhất có nhãn `post_pagination` và SHA-256 nguồn tại đầu lần chạy; lượt này diễn ra trước bản sửa điều phối so sánh và formatter cuối. Các lượt sau được ghi riêng, không gộp thành kết quả của một phiên bản duy nhất. Nội dung đánh giá chi tiết được bổ sung dưới đây sau khi hoàn tất lượt chạy.

## Phạm vi của kiểm tra kỹ thuật

- Bộ backend hiện hữu được chạy với `CUSTOMER_CONSULTATION_ENABLED=false` để kiểm tra hồi quy của đường cũ và không dùng khóa mô hình thật. Đây **không** phải chứng minh ngữ nghĩa của đường tư vấn mới.
- Bộ contract riêng gọi đường tư vấn mới với mô hình giả lập và có test HTTP bật cấu hình; kiểm tra lọc, bộ nhớ, liên kết dữ kiện, tổng ngân sách, kệ demo, đầu ra không hợp lệ và chuyển giao tác vụ giỏ hàng.
- Chạy lại ma trận catalog 1.730 câu sinh theo mẫu trên 346 đường dẫn và 110 lượt API ở đường cũ. Đây là bất biến truy xuất hữu hạn, không phải bao phủ mọi câu tiếng Việt hoặc mọi nhu cầu mua hàng.
- Test frontend và build kiểm tra việc hiển thị, stream, retry và loại bỏ nhãn mô hình khỏi lời chat. Không dùng số test giao diện làm điểm tư vấn.

## Giới hạn phải giữ rõ

- Chưa có thử nghiệm người dùng độc lập hoặc nhân viên cửa hàng chấm mù cách nói. Nhận xét về tự nhiên trong báo cáo là tự rà soát của agent.
- Các đặc tính đường, mùi, hiệu quả giặt, dịu nhẹ, an toàn thú cưng, số lần dùng và hướng dẫn theo từng SKU còn thiếu nhiều dữ liệu nhãn/manufacturer. Tên sản phẩm không thay thế được nguồn này.
- Mô hình vẫn có thể hiểu sai trước bước lọc, hỏi thừa, hoặc đưa câu văn chưa hay. Guard hữu hạn không phải bộ chứng minh mọi khẳng định đều đúng.
- Khi phần văn bị loại, câu chỉ gồm tên/giá có thể còn khô. Cần đánh giá riêng lỗi này thay vì coi “không bịa” là đã “giống nhân viên”.
- Kết quả một lần chạy chịu ảnh hưởng hạn mức và tính không xác định của mô hình. Báo cáo giữ nguyên lỗi thật, không lựa chọn riêng các lượt đẹp để công bố 35/35 đạt.
- Catalog là snapshot giá/tồn từ tháng 09/2026, kệ phần lớn là planogram gợi ý. Demo không chứng minh hàng còn hay vị trí tại cửa hàng hiện tại.

<!-- FINAL_RESULTS -->
## Kết luận kiểm tra thực tế

**Chưa đạt yêu cầu tư vấn như nhân viên thật; chưa sẵn sàng đưa luồng mới vào vận hành.**
Đã chạy đủ 35 câu nguyên văn qua HTTP và 14 hội thoại/34 lượt bổ sung. Không có bảng
35 câu → đáp án hoặc SKU được cài sẵn trong ứng dụng. Việc đọc paper và test code qua
không làm các câu trả lời sai trở thành đúng.

Trong lần `post_pagination`, tự rà soát của agent ghi nhận **17/35 đi đúng hướng ở lượt
đầu, 9/35 còn yếu, 9/35 sai hoặc không xử lý được**. Đây là phân loại tình huống ở phiên
mới, không phải điểm tự nhiên, tỷ lệ tư vấn thành công hay kết quả nhân viên chấm mù.
Nhiều câu thuộc nhóm 17 chỉ hỏi đúng món đang nhắc tới vì phiên chưa có sản phẩm gốc.

- 35/35 trả HTTP 200; **1/35 là fallback tạm thời**, không phải thành công nghiệp vụ.
- 3/35 cần fallback về danh sách tên/giá sau khi phần văn không qua kiểm tra. Danh sách
  có giá đúng vẫn có thể chọn đồ không hợp lý, điển hình câu 8.
- Thời gian API trong lượt này: p50 **1,963 giây**, p95 nearest-rank **6,580 giây**,
  cao nhất **7,243 giây**. Đây là quan sát cục bộ, không phải tải đồng thời/SLA.
- 32 câu dùng `gpt-oss-120b` (gồm 2 facts-fallback), 1 câu dùng `gpt-oss-20b`, 1 dùng
  Qwen và 1 fallback dịch vụ. Log cho thấy Qwen thường chạm rate limit. Không coi
  thay đổi mô hình do hạn mức là một thử nghiệm so sánh mô hình có kiểm soát.

Nguồn đầy đủ: [35 lượt HTTP](artifacts/customer_consultation_35_post_pagination_20261101.json),
[nhận xét có cấu trúc](artifacts/customer_consultation_manual_review_20261101.json),
[14 hội thoại](artifacts/customer_consultation_journeys_20261101.json).

## Những sửa tiếp sau lượt đầy đủ

1. Luồng mới không chặn trước lựa chọn đang chờ, câu hỏi đang được bộ điều khiển cũ
   xử lý, hoặc so sánh khi đã có danh sách hàng. Hai SKU vừa hiển thị được lưu thành
   cặp so sánh. Bộ nhận diện so sánh dùng lại intent chung để hiểu câu hỏi về hai món
   đã xem, kể cả không có dạng “A với B”.
2. So sánh cặp từ phiên tư vấn mới bằng giá/quy cách có nguồn. Không đưa tồn kho vào
   câu hỏi khác biệt nếu khách không hỏi. Có thể tính giá theo 100 g/ml khi tên thể
   hiện một quy cách rõ, cùng chiều đo; bỏ phép quy đổi với lốc/thùng, số món, nhân
   số lượng hoặc tên có quy cách nhập mơ hồ. Không suy từ giá cao ra chất lượng cao.
3. Khi phần văn bị loại nhưng picks hợp lệ, tên/giá fallback nay ghi số lượng nếu
   lớn hơn một và chỉ rõ giá mỗi đơn vị bán, để tổng ngân sách có thể đối chiếu.
4. `CUSTOMER_CONSULTATION_ENABLED=false` là mặc định trong config và `.env.example`.
   Luồng thử nghiệm chỉ chạy khi chủ động bật ở `APP_ENV=development`; môi trường
   production bỏ qua cờ này. Demo đang mở bật cờ riêng ở tiến trình backend để xem thử.

Đây là sửa hành vi chung theo trạng thái, taxonomy và đơn vị đo; không phải đáp án
riêng cho sữa, lẩu hoặc 35 câu. Các sửa này **không khắc phục toàn bộ lỗi ngữ nghĩa**.

Lượt `context_fix` giữ nguyên 6 lượt sau khi sửa định tuyến: câu 14 dùng đúng cặp,
nhưng câu 16/17 còn hỏi xác nhận, câu 18 vẫn nói 950ml lớn hơn 1 lít và suy chất lượng;
câu 19 fallback. Xem [bằng chứng còn lỗi](artifacts/customer_consultation_journeys_context_fix_20261101.json).
Lượt [delivery 2 lượt](artifacts/customer_consultation_journeys_delivery_20261101.json)
chỉ kiểm tra lại thao tác hiển thị cặp → so sánh sau formatter cuối, có hash nguồn.
Không dùng lượt ngắn này để kết luận toàn hành trình đã đạt. Lượt gợi ý đầu tiên vẫn
có thể tự thêm nhận xét về độ tươi, độ béo hoặc thời gian dùng; formatter chỉ đảm bảo
phần so sánh bằng dữ kiện của đường được sửa.

## Rà soát từng câu nguyên văn

“Đúng hướng lượt đầu” chỉ có nghĩa hành động tiếp theo hợp lý trong phiên mới. Các
câu tham chiếu cần đọc thêm bảng nhiều lượt bên dưới. Toàn bộ lời trả lời gốc được
giữ ở phụ lục, không chỉ chọn các câu đẹp để báo cáo.

| # | Câu khách hỏi | Nhận định | Vấn đề/kết quả quan sát |
|---|---|---|---|
| 1 | Anh ơi, nhà em đang thiếu mấy đồ dùng cơ bản, anh tư vấn giúp em với. | Còn yếu | Hỏi vòng lại “cần đồ dùng nào”, chưa đưa nhóm lựa chọn để khách dễ trả lời. |
| 2 | Em muốn mua cái gì đó ăn sáng cho nhanh mà vẫn no, có loại nào ổn không? | Còn yếu | Đúng nhóm ngũ cốc ăn sáng; lời chỉ liệt kê tên/giá, chưa giúp chọn hay xử lý mục tiêu “vẫn no”. |
| 3 | Dạo này trời nóng quá, có loại nước nào uống ngon mà không quá ngọt không? | Còn yếu | Tìm được trà có nhãn không đường; vẫn suy từ nhãn ra độ ngọt và gợi ý mua thêm thiếu lý do. |
| 4 | Nhà em có trẻ con, muốn mua ít đồ ăn vặt mà đỡ ngọt thì chọn gì nhỉ? | Còn yếu | Không bịa đồ an toàn cho trẻ, nhưng hỏi chung chung và không hỏi tuổi. |
| 5 | Em định mua quà cho mẹ mà chưa biết chọn cái gì, tầm 300 nghìn thôi. | Đúng hướng lượt đầu | Hỏi sở thích của mẹ để xác định hướng chọn quà; hợp lý ở lượt đầu. |
| 6 | Có gì ăn tiện mà không cần nấu nướng nhiều không anh? | Còn yếu | Tìm mì/cháo phù hợp hướng ăn tiện, nhưng không giải thích cách lựa chọn có căn cứ; lời khô. |
| 7 | Em muốn mua đồ để dọn nhà cuối tuần, cần những gì nhỉ? | Đúng hướng lượt đầu | Hỏi khu vực cần dọn để thu hẹp loại dụng cụ; câu hỏi có ích. |
| 8 | Em có khoảng 200 nghìn, mua đồ ăn đủ cho hai người một bữa tối được không? | Sai/không xử lý được | Giỏ cháo/phở với rau răm, sả không phải gợi ý bữa tối hợp lý cho hai người, dù tiền không vượt mức. |
| 9 | Có loại dầu gội nào tầm dưới 150 nghìn mà dùng ổn không? | Sai/không xử lý được | Đúng loại hàng và mức giá nhưng tự suy “công thức cao cấp”, chăm sóc sâu và tác dụng ngăn rụng. |
| 10 | Em muốn mua ít đồ ăn cho cả tuần, khoảng 500 nghìn thì nên mua gì? | Sai/không xử lý được | Tự chọn 10 gói mỗi loại mì/sữa chua, chưa biết số người hoặc bữa; tổng đúng không làm kế hoạch cả tuần hợp lý. |
| 11 | Loại nào vừa rẻ vừa dùng được lâu ấy anh? | Đúng hướng lượt đầu | Phiên mới chưa có nhóm hàng; hỏi loại hàng là đúng hướng. Cần kiểm tra riêng khi có lịch sử. |
| 12 | Cái này hơi đắt, có loại nào tương tự mà rẻ hơn không? | Đúng hướng lượt đầu | Phiên mới không biết món gốc; hỏi xác định trước khi tìm hàng thay thế. |
| 13 | Em không cần hàng xịn nhất đâu, loại nào đáng tiền là được. | Đúng hướng lượt đầu | Cần biết loại hàng trước khi đánh giá đáng tiền; không chọn SKU ngẫu nhiên. |
| 14 | Hai loại sữa này khác nhau ở điểm nào vậy anh? | Đúng hướng lượt đầu | Phiên mới chưa có hai SKU nên hỏi cặp cần so. Có lịch sử thì phải dùng cặp vừa xem. |
| 15 | OMO với Ariel thì loại nào giặt sạch hơn nhỉ? | Sai/không xử lý được | Tự thêm quảng cáo, công nghệ giặt, hiệu quả vết bẩn và tiết kiệm bột khi không có nguồn. |
| 16 | Loại này đắt hơn có đáng để mua không? | Đúng hướng lượt đầu | Chưa biết món “này” trong phiên mới; hỏi xác định sản phẩm là cần thiết. |
| 17 | Em thấy hai cái này gần giống nhau, sao giá chênh nhiều thế? | Đúng hướng lượt đầu | Không có cặp sản phẩm ở phiên mới; cần xác định trước khi giải thích chênh giá. |
| 18 | Nếu là anh thì anh chọn loại nào? | Đúng hướng lượt đầu | Không có nhóm/cặp để chọn ở phiên mới; hỏi loại hàng. Đây chưa phải bằng chứng tư vấn lựa chọn tốt. |
| 19 | Cùng một mức giá thì loại nào dùng thích hơn? | Đúng hướng lượt đầu | Chưa biết loại hàng cần so ở phiên mới; hỏi làm rõ. |
| 20 | Da em khá nhạy cảm, có loại sữa tắm nào dịu nhẹ không? | Còn yếu | Không gán dịu nhẹ cho SKU, nhưng hỏi “ưu tiên không gây kích ứng nào” lặp tiêu chí và thiếu bước kiểm tra nhãn có ích. |
| 21 | Mẹ em không thích đồ quá ngọt, có loại bánh nào phù hợp không? | Còn yếu | Hướng bánh mặn/nhãn ít đường hợp lý, nhưng câu hỏi nhiều ý và vẫn lẫn độ ngọt cảm nhận với nhãn đường. |
| 22 | Nhà em có nuôi mèo, muốn mua đồ vệ sinh nhà mà an toàn khi dùng quanh thú cưng. | Còn yếu | Hỏi nhóm vệ sinh là cần thiết; thiếu chỉ dẫn về việc cần đối chiếu hướng dẫn của nhà sản xuất cho thú cưng. |
| 23 | Em đang hạn chế đường, có đồ uống nào phù hợp không? | Sai/không xử lý được | Đưa yến chưng, gọi sai thành nhóm nước ngọt, suy lớn hơn dù cùng tên 65ml, nói “yên tâm về lượng đường”, câu bị cắt. |
| 24 | Em cần loại nhỏ gọn thôi, phòng em bé lắm. | Sai/không xử lý được | Hiểu “phòng em bé lắm” thành phòng của bé; không giữ đúng ý phòng nhỏ. |
| 25 | Em muốn loại không có mùi nồng, dùng hằng ngày ấy. | Sai/không xử lý được | Tự đoán khách cần lăn/xịt khử mùi dù phiên chưa có loại hàng. |
| 26 | Anh ơi, nước giặt ở đâu vậy? | Đúng hướng lượt đầu | Trả vị trí từ trường kệ và ghi rõ planogram demo; không xác nhận đó là vị trí thực tế. |
| 27 | Có bán cái loại sữa hộp màu xanh xanh không nhỉ? | Còn yếu | Không đoán SKU theo màu nhưng hỏi loại sữa quá chung, chưa giúp nhận diện bằng hãng/quy cách. |
| 28 | Loại mì Hàn Quốc cay cay hôm trước em mua còn không? | Đúng hướng lượt đầu | Hỏi hãng/tên để nhận diện mì; không nhận có lịch sử lần mua ngoài phiên. |
| 29 | Em tìm mãi không thấy khăn giấy, nó nằm khu nào vậy? | Sai/không xử lý được | Fallback tạm thời thay cho chỉ vị trí; HTTP 200 nhưng không xử lý được yêu cầu. |
| 30 | Cái loại 1 lít ấy, chứ không phải chai nhỏ đâu. | Sai/không xử lý được | Tự thêm nhóm dầu khi chỉ biết 1 lít, không có ngữ cảnh. |
| 31 | Có sản phẩm nào tương tự không, nếu cái em cần hết hàng rồi? | Đúng hướng lượt đầu | Hỏi món gốc trước khi tìm sản phẩm tương tự ở phiên mới. |
| 32 | Anh ơi, cái loại hay quảng cáo trên TikTok ấy, bên mình có không? | Đúng hướng lượt đầu | Hỏi loại hàng để nhận diện quảng cáo; không coi TikTok xác định được SKU. |
| 33 | Em muốn mua loại tốt tốt một chút, nhưng đừng đắt quá. | Đúng hướng lượt đầu | Hỏi loại sản phẩm trước khi tư vấn mức chất lượng/giá. |
| 34 | Có cái nào dùng được cho cả nhà không? | Đúng hướng lượt đầu | Chưa có nhóm hàng nên hỏi loại dùng cho cả nhà, chưa khẳng định an toàn mọi lứa tuổi. |
| 35 | Hôm trước em mua loại màu vàng ấy, giờ có loại nào tốt hơn không? | Đúng hướng lượt đầu | Không đoán sản phẩm hoặc lịch sử dựa trên màu vàng; hỏi nhận diện trước. |

## Hội thoại nhiều lượt và cách diễn đạt mới

Đây là 14 hành trình được viết để phát triển, không phải dữ liệu mù. Các lượt về sổ,
pin, khăn lau kính và đồ tắm chó không có SKU đích đặt trước. Không dùng “đúng SKU đích”
làm tiêu chí duy nhất; phải xem món có hợp nhu cầu và lời giải thích có bằng chứng không.

| Hội thoại | Lượt | Nhận xét thực tế trước sửa điều phối cuối |
|---|---:|---|
| milk-comparison | 6 | Đã hiển thị hai sữa nhưng hỏi lại tên; các lượt sau lặp xác nhận, nói sai giá trị/quy cách và vẫn không giúp chọn. Bản sửa sau đó xử lý riêng đường so sánh cặp đã biết, chưa sửa toàn hành trình. |
| shampoo-value | 6 | Giữ nhóm nhưng bịa thời gian dùng; hỏi lại ngân sách đã có; có lỗi xưng hô với trẻ em. |
| volume-reference | 3 | Lượt đầu fallback; lượt 1 lít chỉ hỏi xác nhận thay vì tìm đúng quy cách; thay thế chưa hiệu quả. |
| small-space-low-scent | 3 | Giữ được nước lau sàn; câu trả lời về phòng nhỏ bị cắt, chưa chọn được theo kích thước/mùi. |
| household-discovery | 2 | Đã tìm đúng dụng cụ bếp ở lượt sau nhưng tự nói dùng được với mọi bề mặt và làm sạch nhanh. |
| weekly-budget | 2 | Tự ghép giỏ rẻ thiếu hợp lý; sau khi biết hai người/bữa tối vẫn đưa cháo với rau răm/sả. |
| child-snacks | 2 | Không tự đưa hàng cay, nhưng lời từ chối dài, không hỏi tuổi đúng lúc; sau khi biết tuổi vẫn hỏi nhãn chung chung. |
| pet-cleaning | 2 | Không gán an toàn cho SKU nhưng hỏi xác nhận điều khách đã nói, chưa đưa bước tiếp theo hữu ích. |
| hotpot-meat | 2 | Đúng bò cắt lát, không có bánh bao; vẫn tự suy mềm/nhanh chín/đủ gia đình và thành phần bộ lẩu. |
| gift-feedback | 2 | Giữ sở thích ít ngọt nhưng tiếp tục hỏi xác nhận, chưa có lựa chọn dựa trên bằng chứng nhãn. |
| unseen-stationery | 1 | Tư vấn A4 cho nhu cầu mang túi và tự gán hai sổ có bìa bồi/bảo vệ tốt. |
| unseen-battery | 1 | Đưa cả AA/AAA trước khi xác định loại pin remote; giá đúng nhưng tương thích chưa biết. |
| unseen-towel | 1 | Không bịa chống xơ; hỏi chất liệu/ngân sách còn khô và chưa nêu cách chọn có ích. |
| unseen-pet-grooming | 1 | Không chọn SKU thiếu nguồn, nhưng chỉ hỏi lại yêu cầu không kích ứng, chưa tiến triển. |

## Kiểm tra kỹ thuật và phạm vi bằng chứng

| Kiểm tra | Kết quả | Giới hạn/cấu hình |
|---|---|---|
| Bộ backend đầy đủ | 1.618 passed, 1 skip | Luồng tư vấn mới tắt; chạy trước các sửa định tuyến/formatter cuối. Skip do Windows không cho tạo symlink. Không phải điểm tư vấn thật. |
| Hồi quy định tuyến/API | 512 passed | Sau sửa định tuyến và cờ mặc định; trước formatter cuối. Bao gồm các test HTTP chủ động bật luồng mới với provider giả lập. |
| Contract + nhận diện so sánh cuối | 71 passed | 40 test consultation và 31 test so sánh; không gọi mô hình thật. Kiểm tra trạng thái, dữ kiện, ngân sách, phân trang và quy đổi đơn vị. |
| Frontend | 100 passed/9 file | Kiểm tra hiển thị/stream/retry và bỏ badge mô hình; build thành công. Không phải chấm văn phong. |
| Quét catalog | 1.730 mẫu/346 đường dẫn và 110 lượt API, 0 failure | Luồng cũ tắt semantic, kiểm tra bất biến truy xuất hữu hạn. Không bao phủ mọi ngành hàng/ý định. |
| Ruff, compileall, diff check | Qua | Chất lượng mã/định dạng, không chứng minh nội dung tư vấn đúng. |

Không cộng các số test vì có phần trùng nhau. Artifact:
[backend](artifacts/customer_consultation_backend_final_20261101.txt),
[routing](artifacts/customer_consultation_routing_regression_20261101.txt),
[contract cuối](artifacts/customer_consultation_context_gate_20261101.txt),
[frontend](artifacts/customer_consultation_frontend_final_20261101.txt),
[build](artifacts/customer_consultation_build_final_20261101.txt),
[catalog](artifacts/customer_consultation_catalog_gate_20261101.json).

## Việc còn phải giải quyết trước khi bật mặc định

- **Chọn món theo mục đích và lập giỏ:** tổng tiền đúng chưa đủ. Cần kiểm tra vai trò
  từng món, số người/bữa và việc giỏ gợi ý có tạo thành bữa ăn hợp lý. Rau gia vị không
  thể thay phần rau/chất đạm của bữa chỉ vì cùng ngành thực phẩm và giá rẻ.
- **Giải thích có bằng chứng:** giá/quy cách có thể tính; công dụng, hiệu quả giặt,
  mùi, độ ngọt cảm nhận, nhạy cảm da, an toàn thú cưng và tương thích pin cần trường
  nhãn/nhà sản xuất hoặc hỏi đúng thông tin còn thiếu. Guard regex hiện tại không đủ
  chặn mọi diễn đạt sai. Cần ràng buộc từng phát biểu vào nguồn trước khi mở rộng.
- **Hỏi để tiến triển:** bỏ câu hỏi xác nhận lại nhu cầu đã có, hỏi ngân sách lại và
  hỏi quá chung. Phân biệt thông tin thật sự quyết định lựa chọn với thông tin tùy chọn;
  có thể gợi ý vài hướng trước khi xin thêm một chi tiết.
- **Bộ nhớ và đổi nhu cầu:** cần tiếp tục kiểm tra so sánh đáng tiền, đồ cho cả nhà,
  chọn thay thế và quy cách sau nhiều lượt. Sửa một câu tham chiếu chưa xác nhận cả
  chuỗi đã giải quyết được việc mua hàng.
- **Xếp hạng và độ phủ:** tập ứng viên còn thiên về giá thấp; giới hạn 200 dòng mỗi
  nhu cầu có thể bỏ sót hàng trong nhóm lớn. Không kết luận “hết/không có hàng” từ
  việc bộ truy xuất giới hạn không tìm được. Cần đo độ phủ và xếp hạng theo nhiệm vụ.
- **Đánh giá người thật:** cần hội thoại mới, nhân viên/người Việt chấm các chiều
  đúng nhu cầu, có ích, liên tục, dễ nói chuyện và không bịa. Chưa có bằng chứng này.

## Cách tái hiện

Chạy backend ở thư mục gốc dự án, cần khóa mô hình đã cấu hình:

```powershell
$env:APP_ENV='development'
$env:CUSTOMER_CONSULTATION_ENABLED='true'
$env:DEMO_SALES_ENABLED='true'
uv run uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
```

Ở terminal khác:

```powershell
uv run python -m evaluation.run_customer_consultation_review --label review_new --delay-seconds 5
uv run python -m evaluation.run_customer_consultation_journeys --label review_new --delay-seconds 8
uv run pytest tests/test_customer_consultation.py tests/test_product_comparison.py -q
```

Tắt cờ và khởi động lại backend để so với đường cũ. Đổi `--label` để giữ các kết quả
trước; mỗi JSON lưu hash nguồn. Các file tên `final`, `final_report`, `delivery` của
runner 35 câu có từ những lần phát triển trung gian; tên file không phải chứng nhận
đạt chất lượng. Báo cáo này dùng đúng `post_pagination`, annotation có nguồn và lượt
`journeys_delivery` sau formatter, không thay nội dung của các file lịch sử.

Demo: <http://127.0.0.1:5173/>. Ảnh trạng thái kiểm tra:
[customer_consultation_demo_20261101.png](artifacts/customer_consultation_demo_20261101.png).
Đây là bản thử nghiệm, không phải kết luận đã xử lý tốt 35/35.

## Phụ lục: lời trả lời thực tế của 35 câu

Giữ nguyên văn bản phản hồi ở lần `post_pagination`, kể cả lỗi diễn đạt và câu bị cắt.
Metadata ID, thẻ hàng, model, thời gian và hash nguồn nằm trong JSON gốc.

### Câu 1

Khách: Anh ơi, nhà em đang thiếu mấy đồ dùng cơ bản, anh tư vấn giúp em với.

> Bạn đang cần những đồ dùng nào?

### Câu 2

Khách: Em muốn mua cái gì đó ăn sáng cho nhanh mà vẫn no, có loại nào ổn không?

> Mình gợi ý bạn xem Ngũ cốc ăn sáng Nestlé Honey Star hộp 150g (53.500đ); Ngũ cốc ăn sáng Koko Krunch Nestlé hộp 170g (54.500đ). Giá tham khảo; bạn kiểm tra giá và hàng tại quầy nhé.

### Câu 3

Khách: Dạo này trời nóng quá, có loại nước nào uống ngon mà không quá ngọt không?

> Bạn có thể thử Lốc 6 chai trà Oolong không đường Tea+ 455ml – trà Oolong không đường, 455 ml/chai, giá 60.500đ. Đây là lựa chọn không đường, phù hợp với nhu cầu nước giải khát nhẹ, không quá ngọt trong thời tiết nóng. Nếu muốn thêm, bạn có thể mua thêm vài gói để luôn có sẵn trong tủ lạnh. Giá tham khảo; bạn kiểm tra giá và hàng tại quầy nhé.

### Câu 4

Khách: Nhà em có trẻ con, muốn mua ít đồ ăn vặt mà đỡ ngọt thì chọn gì nhỉ?

> Hi bạn, hiện chưa có thông tin chi tiết về vị, thành phần dinh dưỡng và an toàn cho trẻ, nên mình chưa thể đề xuất món cụ thể. Bạn có thể cho mình biết thêm về yêu cầu về hương vị hoặc thành phần ưu tiên không?

### Câu 5

Khách: Em định mua quà cho mẹ mà chưa biết chọn cái gì, tầm 300 nghìn thôi.

> Mẹ thích gì hơn, thực phẩm, mỹ phẩm hay đồ dùng gia đình?

### Câu 6

Khách: Có gì ăn tiện mà không cần nấu nướng nhiều không anh?

> Mình gợi ý bạn xem Mì Kokomi Đại 90 Tôm chua cay  gói 90g (4.400đ); Cháo gà Vifon gói 50g (4.000đ); Mì tôm chanh Omni gói 75g (4.700đ). Giá tham khảo; bạn kiểm tra giá và hàng tại quầy nhé.

### Câu 7

Khách: Em muốn mua đồ để dọn nhà cuối tuần, cần những gì nhỉ?

> Bạn muốn dọn dẹp khu vực nào trong nhà?

### Câu 8

Khách: Em có khoảng 200 nghìn, mua đồ ăn đủ cho hai người một bữa tối được không?

> Mình gợi ý bạn xem Cháo gà Vifon gói 50g (4.000đ); Phở chay rau nấm Vifon gói 65g (9.800đ); Rau Răm 50g (3.500đ); Củ Sả WinEco 100g (4.000đ). Giá tham khảo; bạn kiểm tra giá và hàng tại quầy nhé.

### Câu 9

Khách: Có loại dầu gội nào tầm dưới 150 nghìn mà dùng ổn không?

> Bạn có thể thử Dầu gội Clear Men mát lạnh bạc hà 180g/170g 80.000đ – giá nhẹ, mùi bạc hà giúp da đầu cảm giác mát lạnh; Dầu gội cao cấp Romano classic chai 380g 127.600đ – công thức cao cấp, phù hợp nếu muốn chăm sóc sâu hơn mà vẫn trong mức giá; và Dầu gội Pantene Ngăn rụng tóc 300g 104.000đ – hỗ trợ ngăn rụng tóc, giá trung bình. Ba lựa chọn này đều dưới 150 nghìn, đáp ứng nhu cầu gội đầu hàng ngày mà không vượt ngân sách. Giá tham khảo; bạn kiểm tra giá và hàng tại quầy nhé.

### Câu 10

Khách: Em muốn mua ít đồ ăn cho cả tuần, khoảng 500 nghìn thì nên mua gì?

> Bạn có thể mua 10 gói mỗi loại: Mì ăn liền Koreno vị kim chi Paldo gói 100g 12.200đ, Sữa chua ăn TH Milk topkid chuối lúa mạch 60g 7.100đ, Sữa chua ăn Vinamilk Love Yogurt Green Farm ít đường hộp 100g 8.200đ, Sữa chua ăn Vinamilk lựu Đỏ Ít Đường 100g 9.300đ, Sữa chua ăn LoveYogurt Trân Châu Đường Đen hộp 100g 10.000đ. Tổng chi phí khoảng 468.000đ, vừa trong ngân sách 500 nghìn. Các món này đa dạng từ mì ăn liền đến sữa chua, giúp bạn có lựa chọn nhanh gọn cho bữa ăn và bữa phụ suốt cả tuần. Giá tham khảo; bạn kiểm tra giá và hàng tại quầy nhé.

### Câu 11

Khách: Loại nào vừa rẻ vừa dùng được lâu ấy anh?

> Bạn đang tìm loại nào?

### Câu 12

Khách: Cái này hơi đắt, có loại nào tương tự mà rẻ hơn không?

> Bạn đang nói tới sản phẩm nào cụ thể?

### Câu 13

Khách: Em không cần hàng xịn nhất đâu, loại nào đáng tiền là được.

> Bạn đang muốn mua loại nào?

### Câu 14

Khách: Hai loại sữa này khác nhau ở điểm nào vậy anh?

> Bạn đang muốn so sánh hai loại sữa nào?

### Câu 15

Khách: OMO với Ariel thì loại nào giặt sạch hơn nhỉ?

> Bạn có thể thử Bột giặt ARIEL đam mê túi 650g (Bột giặt ARIEL đam mê túi 650g, 42.300đ) và Bột giặt thông minh Omo gói 800g/770g (Bột giặt thông minh OMO gói 800g/770g, 42.300đ). ARIEL thường được quảng cáo mạnh về khả năng loại bỏ vết bẩn sâu, trong khi OMO nhấn mạnh công nghệ bọt thông minh giúp bột lan đều, giảm lượng bột dùng. Nếu ưu tiên giặt sạch vết bẩn cứng đầu, ARIEL có thể phù hợp hơn; nếu muốn tiết kiệm bột và dễ hòa tan, OMO là lựa chọn tốt. Bạn muốn thử một trong hai hoặc cả hai để tự đánh giá? Giá tham khảo; bạn kiểm tra giá và hàng tại quầy nhé.

### Câu 16

Khách: Loại này đắt hơn có đáng để mua không?

> Bạn đang nói tới loại nào?

### Câu 17

Khách: Em thấy hai cái này gần giống nhau, sao giá chênh nhiều thế?

> Bạn đang so sánh hai sản phẩm nào?

### Câu 18

Khách: Nếu là anh thì anh chọn loại nào?

> Bạn đang muốn tìm loại nào cụ thể để mình gợi ý nhé?

### Câu 19

Khách: Cùng một mức giá thì loại nào dùng thích hơn?

> Bạn đang muốn so sánh loại nào trong cùng mức giá?

### Câu 20

Khách: Da em khá nhạy cảm, có loại sữa tắm nào dịu nhẹ không?

> Bạn muốn sữa tắm dịu nhẹ cho da nhạy cảm, có ưu tiên không mùi hay không gây kích ứng nào không?

### Câu 21

Khách: Mẹ em không thích đồ quá ngọt, có loại bánh nào phù hợp không?

> Mẹ bạn muốn bánh ít đường hoặc không đường, có muốn bánh ngọt nhẹ hay bánh mặn?

### Câu 22

Khách: Nhà em có nuôi mèo, muốn mua đồ vệ sinh nhà mà an toàn khi dùng quanh thú cưng.

> Bạn muốn mua loại sản phẩm nào cho việc vệ sinh nhà?

### Câu 23

Khách: Em đang hạn chế đường, có đồ uống nào phù hợp không?

> Bạn ơi, bên mình có 2 lựa chọn đồ uống không đường phù hợp với nhu cầu hạn chế đường của bạn:
> 
> 1. Tổ yến nguyên chất chưng sẵn không đường Yến Việt hộp 65ml - 47.500đ/lọ: Đây là yến chưng sẵn không đường, đóng hộp nhỏ 65ml, giá phải chăng, tiện lợi cho việc dùng thử hoặc dùng cá nhân.
> 
> 2. Tổ yến nguyên chất chưng sẵn Yến Việt không đường hộp 65ml - 230.000đ/hộp: Cũng là yến chưng sẵn không đường 65ml, nhưng đóng gói theo hộp lớn hơn, có thể phù hợp nếu bạn dùng thường xuyên.
> 
> Cả hai đều thuộc nhóm nước ngọt nhưng ghi rõ không đường, nên bạn có thể yên tâm về lượng đường. Bạn muốn dùng để Giá tham khảo; bạn kiểm tra giá và hàng tại quầy nhé.

### Câu 24

Khách: Em cần loại nhỏ gọn thôi, phòng em bé lắm.

> Bạn đang tìm món đồ nào cho phòng bé?

### Câu 25

Khách: Em muốn loại không có mùi nồng, dùng hằng ngày ấy.

> Bạn muốn dùng loại lăn hay xịt khử mùi cho nam hay nữ?

### Câu 26

Khách: Anh ơi, nước giặt ở đâu vậy?

> Bạn tìm ở khu Chất giặt tẩy nhé. Trên sơ đồ demo, một vị trí của nhóm này là A03-B04-L03-P07; mình chưa xác nhận được vị trí thực tế tại cửa hàng.

### Câu 27

Khách: Có bán cái loại sữa hộp màu xanh xanh không nhỉ?

> Bạn muốn mua sữa hộp loại nào?

### Câu 28

Khách: Loại mì Hàn Quốc cay cay hôm trước em mua còn không?

> Bạn muốn tìm mì Hàn Quốc cay mà hôm trước mua, có thể cho biết thương hiệu hoặc tên cụ thể không?

### Câu 29

Khách: Em tìm mãi không thấy khăn giấy, nó nằm khu nào vậy?

> Mình đang tra hàng hơi chậm. Bạn nhắn lại giúp mình yêu cầu vừa rồi nhé?

### Câu 30

Khách: Cái loại 1 lít ấy, chứ không phải chai nhỏ đâu.

> Bạn muốn mua loại dầu nào 1 lít?

### Câu 31

Khách: Có sản phẩm nào tương tự không, nếu cái em cần hết hàng rồi?

> Bạn đang muốn tìm sản phẩm thay thế nào?

### Câu 32

Khách: Anh ơi, cái loại hay quảng cáo trên TikTok ấy, bên mình có không?

> Bạn đang muốn tìm loại sản phẩm nào trên TikTok?

### Câu 33

Khách: Em muốn mua loại tốt tốt một chút, nhưng đừng đắt quá.

> Bạn đang tìm sản phẩm nào cụ thể?

### Câu 34

Khách: Có cái nào dùng được cho cả nhà không?

> Bạn đang muốn tìm sản phẩm nào dùng cho cả nhà?

### Câu 35

Khách: Hôm trước em mua loại màu vàng ấy, giờ có loại nào tốt hơn không?

> Bạn đang muốn tìm loại gì màu vàng?
