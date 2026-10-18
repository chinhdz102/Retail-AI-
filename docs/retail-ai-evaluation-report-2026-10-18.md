# Báo cáo đánh giá Retail AI — 18/10/2026

## Kết luận

Các luồng backend chính, kiểm soát dữ liệu và hồi quy đạt bộ kiểm thử tự động. Sau khi hoàn thiện nguồn dữ liệu doanh nghiệp, định tuyến đơn vị vận hành và chặn các biến thể hỏi địa điểm chung, vòng kiểm tra mới nhất chạy **411/411 pytest pass**, **40/40 P0 regression pass**, Ruff và compile pass. Regression runner trước đó phát hiện một lỗi định tuyến tồn kho; lỗi đã được sửa và kiểm tra lại.

Các tính năng thông tin chi nhánh, khuyến mãi đang chạy, hàng vừa nhập và địa điểm gần cửa hàng đã có schema, tool và kiểm tra freshness/provenance. Tuy vậy, PostgreSQL hiện không kết nối được và chưa có dữ liệu chi nhánh/feed được xác minh. Vì vậy các tính năng này **chưa thể cung cấp dữ liệu thật cho kiosk**. Catalog hiện có 3.099 dòng nhưng lần thu thập mới nhất là 15/09/2026; khuyến mãi dạng text không chứng minh chương trình còn hiệu lực.

## Phạm vi và cách chạy

- Chạy pytest với `CATALOG_REPOSITORY=demo`; bộ này kiểm tra API, intent, grounding, an toàn, importer, công cụ thông tin chi nhánh và planogram.
- Chạy P0 regression qua backend với phản hồi LLM giả lập, 8 lỗi ưu tiên × 5 lượt; không tiêu quota Groq.
- Chạy benchmark text trực tiếp với Groq thật trên hội thoại tiếng Việt: chào hỏi nhiều lượt, câu hỏi giá kiểu chat địa phương và hỏi giờ đóng cửa khi chưa có hồ sơ chi nhánh. Không gửi audio.
- Kiểm tra kết nối PostgreSQL với timeout 2 giây; không truy vấn hay in thông tin đăng nhập.
- Kiểm tra catalog bằng `analyze_dataset` ở chế độ đọc, không ghi dữ liệu vào DB.

## Kết quả kiểm tra

| Hạng mục | Kết quả | Ý nghĩa |
|---|---:|---|
| Toàn bộ pytest | **411 passed** | Không còn test fail; bao gồm provenance doanh nghiệp, định tuyến và các biến thể hỏi địa điểm chung. |
| P0 regression | **40/40 passed** | Tồn kho, small talk, ràng buộc đúng sản phẩm, chặn sửa giá/tồn kho/thanh toán giả, timeout và hàng hết hạn; mỗi ca lặp 5 lần. |
| Live Groq text | **5/5 lượt pass**, 3 tình huống | 4/4 quyết định không gọi tool đúng; câu hỏi giá viết kiểu “nhiu z” gọi đúng tool model 1/1. |
| Live Groq latency | Median turn **5,41 giây**; median lần gọi model ghi nhận **577 ms** | Turn latency có thể gồm pacing và fallback; không phải time-to-first-token. Không đo STT, TTS hoặc thời gian hiển thị trên trình duyệt. |
| Fallback Groq | **1 lần rate limit** | Qwen bị rate limit ở câu hỏi giá; fallback `openai/gpt-oss-120b` trả lời thành công. |
| Ruff | **Pass** | `ruff check backend tests scripts`. |
| Compile | **Pass** | `compileall backend tests`. |
| Diff whitespace | **Pass** | `git diff --check`; Git chỉ in cảnh báo chuẩn hóa LF/CRLF trên Windows. |

### Bổ sung sau vòng hoàn thiện dữ liệu

- Mốc sau hoàn thiện hồ sơ doanh nghiệp là 399/399; sau khi mở rộng nhận diện hỏi địa điểm chung, kết quả mới nhất là **411/411 pytest pass**. P0 vẫn **40/40**.
- Câu hỏi như “Ai vận hành cửa hàng này?” và “Cửa hàng này thuộc công ty nào?” được định tuyến xác định tới hồ sơ WinCommerce trong ngữ cảnh chuỗi WinMart, kèm liên kết nguồn chính thức. Kiểm thử xác nhận luồng không gọi LLM.
- “Đồ án FPT/ChatVRM” được phân giải thành hồ sơ dự án từ thông tin nhóm cung cấp; “FPT” đứng riêng vẫn phân giải thành FPT Corporation. Claim dự án có provenance riêng và không khẳng định FPT tài trợ, sở hữu hoặc hợp tác chính thức.
- Hồ sơ doanh nghiệp chuyển sang claim-level provenance: mỗi claim khai báo source ID; dữ liệu không hợp lệ nếu thiếu nguồn, tham chiếu nguồn lạ, ID trùng, hoặc nguồn công khai thiếu URL/ngày xác minh. Facts công khai quá 180 ngày hoặc có ngày xác minh trong tương lai bị loại khỏi câu trả lời.
- Hỏi đáp địa điểm chung đã bị loại khỏi luồng kiến thức: API trả lời chính sách rõ ràng, không gọi LLM hay POI tool. Lookup địa điểm quanh một chi nhánh cụ thể vẫn là luồng riêng, yêu cầu snapshot có nguồn và dữ liệu vị trí đã xác minh.
- Guard địa điểm chung kiểm tra cả cụm vị trí và loại địa điểm, gồm các cách hỏi như “quán ăn gần đây”, “hiệu thuốc gần nhất”, “ATM nearby”, “restaurants near me”, “nhà hàng quanh khu vực này” và “closest Circle K”; câu hỏi không gắn chi nhánh không được chuyển cho LLM đoán.

Live benchmark chi tiết tạm thời nằm trong `.tmp/live_eval_text_20261018.json`; thư mục `.tmp` không được đưa vào commit. Kết quả được tóm tắt tại đây để repo chỉ có một file báo cáo mới.

### Lỗi hồi quy tìm thấy và sửa

P0 ban đầu chỉ đạt 35/40. Câu **“Coca Zero còn bao nhiêu?”** bị nhận nhầm thành câu hỏi giá: bước chuẩn hóa biến “bao nhiêu” thành “bao bao nhiêu”, sau đó bộ nhận diện chọn `price_vnd`. Agent trả giá thay vì số lượng tồn.

Đã sửa chuẩn hóa để không viết lại từ “nhiêu” nằm trong cụm “bao nhiêu”, đồng thời phân biệt “còn bao nhiêu” (số lượng) với “giá … còn bao nhiêu” hoặc “còn bao nhiêu tiền” (giá). Thêm ba biến thể kiểm tra: câu tồn kho chuẩn, viết tắt “còn nhiu”, và câu hỏi giá còn bao nhiêu tiền. Sau sửa, vòng trước đạt 394/394 và P0 đạt 40/40; phần hoàn thiện doanh nghiệp đạt 399/399; vòng địa điểm đạt 408/408 rồi 411/411 sau khi thêm truy vấn cửa hàng tiện lợi và Circle K. P0 vẫn 40/40.

## Đánh giá các chức năng retail

| Chức năng | Mức hỗ trợ trong code | Trạng thái dữ liệu lúc đánh giá |
|---|---|---|
| Tìm sản phẩm, tư vấn giá, tồn kho và thuộc tính catalog | Có search/snapshot/recommendation; câu trả lời facts được ràng buộc với snapshot đã tra; thiếu field thì nói rõ chưa xác minh. | Demo và unit tests hoạt động. PostgreSQL timeout nên không xác nhận được tra cứu catalog production trong lần này. |
| Gợi ý sản phẩm | Có lọc theo danh mục, ngân sách, cay/không cay và tồn kho bán được; câu hỏi mơ hồ có thể hỏi lại. | Có test API/tool và live Qwen probe trên catalog demo; chưa đo chất lượng thống kê trên hội thoại người dùng thật. |
| Vị trí kệ | Có vị trí dạng `Axx-Bxx-Lxx-Pxx` và kiểm tra phiên bản dữ liệu planogram. | Shelf trong catalog nguồn là placeholder; vị trí đề xuất không phải xác nhận kệ vật lý. Ảnh 3D là sơ đồ minh họa; mã A01–A13 và tên khu lấy từ planogram/catalog, còn hình học và tọa độ nhãn là dữ liệu trình bày. |
| Khuyến mãi / chương trình giảm giá | Có truy vấn campaign theo kiosk, thời gian hiệu lực, phạm vi cửa hàng và trạng thái feed. Câu hỏi sản phẩm cụ thể cũng có thể trả promotion đã ghi trong snapshot kèm ngày nguồn. | Chưa có feed campaign hoạt động. 580 dòng catalog có text khuyến mãi là snapshot cũ, không được dùng để khẳng định ưu đãi đang chạy. |
| Sản phẩm mới trong danh mục | Có so sánh hai lần import hoàn chỉnh; import chưa phân loại đủ các dòng nguồn không được coi là hoàn chỉnh. | Chưa có lịch sử hai import hoàn chỉnh để trả danh sách mới. “Mới thêm vào catalog” không bị đổi thành “mới ra mắt thị trường”. |
| Hàng vừa nhập kho | Có tool nhận sự kiện receipt theo cửa hàng, mã hàng, số lượng, thời điểm và chứng từ; feed phải tươi và đủ cửa sổ 7 ngày mới được kết luận không có hàng mới. | Chưa có receipt/feed thật. Không suy diễn hàng nhập từ chênh lệch tồn kho. |
| Thông tin cửa hàng | Có store master và ánh xạ kiosk → chi nhánh; chỉ dùng hồ sơ còn hạn xác minh. | Chưa có bản ghi chi nhánh/kiosk đã xác minh. Câu hỏi giờ mở cửa phải báo thiếu dữ liệu thay vì tự bịa giờ. |
| Giới thiệu doanh nghiệp | Có hồ sơ riêng cho WinCommerce, FPT và đồ án ChatVRM; resolver ưu tiên alias dự án như “đồ án FPT/ChatVRM”, còn FPT đứng riêng là FPT Corporation. Câu hỏi đơn vị vận hành cửa hàng mặc định về WinCommerce; mỗi claim công khai có source riêng, ngày xác minh và link trong câu trả lời. | Facts dự án dựa trên thông tin nhóm cung cấp, được gắn nhãn rõ. Không suy ra quan hệ hợp tác chính thức giữa đồ án và FPT. |
| Hỏi đáp địa điểm chung | Đã loại khỏi customer agent; chỉ trả chính sách nêu rõ phạm vi không hỗ trợ, không gọi model/search/geocoder. | Không tra cứu địa điểm chung. |
| Địa điểm gần một cửa hàng cụ thể | Có lookup POI theo tọa độ chi nhánh và snapshot có attribution, hạn dùng; bắt buộc gắn với một chi nhánh. | Chưa có tọa độ chi nhánh/POI feed. Luồng cụ thể chưa thể trả kết quả thật đến khi có dữ liệu đã xác minh. |
| Chăm sóc khách hàng / phân tích tương tác | Các câu hỏi dịch vụ được xử lý bằng phản hồi chính sách an toàn; request quản trị/thanh toán giả bị chặn. | Chưa có quy trình chuyển nhân viên thực, lưu khiếu nại hoặc pipeline thống kê câu hỏi/sản phẩm được hỏi nhiều. Agent không được tuyên bố đã báo nhân viên hay ghi nhận ticket. |
| Tiếng Anh | Có nhánh trả lời tiếng Anh cho một số chức năng store/company/catalog và test tương ứng. | Benchmark live trong báo cáo này chỉ dùng tiếng Việt; chưa đánh giá bao phủ tiếng Anh đầu-cuối. |

## Đánh giá dữ liệu catalog và planogram

Đọc kiểm tra `data/winmart_products.json` cho kết quả:

- 3.099 dòng nguồn; 3.081 dòng hợp lệ để import; 18 dòng được quarantine vì giá khuyến mãi cao hơn giá thường.
- 74 danh mục, 699 brand; 580 dòng có chuỗi `discount_promotion`.
- Thời điểm scrape mới nhất: **15/09/2026 13:53 UTC**. Đây là thời điểm snapshot crawler, không phải ngày nhập hàng hay ngày ra mắt sản phẩm.
- Code đánh dấu shelf nguồn `SIMULATED`; không sử dụng các vị trí này như vị trí kệ vật lý đã xác minh.

Planogram đề xuất giữ nhóm department/sub-department/category, gán một locator cho SKU hợp lệ và đặt nhãn đầu kệ A01–A13. Chưa có số liệu fixture thực tế, kích thước hàng, doanh số, biên lợi nhuận hay basket affinity; vì vậy layout không thể được gọi là tối ưu doanh thu hoặc bản vẽ thi công. Xem [phương pháp planogram](planogram-method.md) và [ảnh gắn nhãn từng đầu kệ](../data/winmart_planogram_3d_each_aisle.png).

## Injection và chất lượng hội thoại

Unit tests xác nhận policy chặn prompt giả quyền quản trị, lấy thông tin nội bộ và hành động sửa giá/tồn kho/thanh toán; nội dung injection bị từ chối không được đưa vào lịch sử model như chỉ dẫn hợp lệ. Live probe hiện tại pass 5/5 câu kiểm tự động, nhưng cỡ mẫu nhỏ nên không phải tỉ lệ lỗi ngoài thực tế.

Một trace Groq tiếng Việt rộng hơn đã lưu ngày 18/10/2026 có 16 lượt, **15/16 assertion tự động pass**. Lượt thiếu là tiêu chí model tự chọn tool ở câu nối tiếp yêu cầu gợi ý snack; API vẫn gọi `recommend_products` + `get_product_snapshot` bằng route xác định và trả đúng `SNACK01`, nhưng model trace không phát tool-call. Đây là khoảng cách ở chỉ số **model planning**, không phải câu trả lời sai trong lượt đó. Cần giữ đánh giá này riêng với khả năng của backend routing.

## Giới hạn và việc cần làm trước khi demo dữ liệu thật

1. PostgreSQL trả `TimeoutError` trong probe 2 giây; chưa chạy được truy vấn thật hay kiểm tra schema/feed trên server. Vì vậy các tính năng chi nhánh, khuyến mãi, receipt, POI và catalog production chưa được xác nhận live.
2. Cần nạp store master, ánh xạ từng kiosk, feed-health có coverage, campaign có ngày/điều khoản, receipt events và POI snapshot có attribution. Khi chưa có các bản ghi này, phản hồi “chưa xác minh” là hành vi đúng.
3. Nên chạy lại benchmark PostgreSQL trên môi trường DB đang hoạt động và đánh giá model/tool-selection nhiều lượt sau khi dữ liệu được đồng bộ.
4. Báo cáo này đánh giá text agent/backend và asset planogram. Nó không đo microphone trên trình duyệt, STT, TTS, streaming UI, VRM lip-sync, latency mạng thực hoặc người dùng thật.

## Lệnh tái lập

```powershell
$env:CATALOG_REPOSITORY='demo'
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check backend tests scripts
.venv\Scripts\python.exe -m compileall -q backend tests
```

P0 runner có thể gọi bằng `asyncio.run(evaluation.run_p0_regressions.benchmark())` để lấy kết quả mà không ghi đè file kết quả đã theo dõi trong repo.
