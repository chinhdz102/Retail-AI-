# Kiểm tra tư vấn backend bằng text — 10/11/2026

**Đã sửa các lỗi luồng tư vấn và ngữ cảnh, nhưng chưa đủ cơ sở gọi chất lượng
là “nhân viên thật”.** Báo cáo này chấm câu trả lời thực tế, không lấy HTTP 200
hay unit test làm bằng chứng tư vấn đúng. Câu trả lời nguyên văn và đánh giá
từng lượt nằm trong artifact được liên kết ở cuối báo cáo.

## Phạm vi và dữ liệu

- Chạy tại worktree cũ `vietnamese-whisper-stt/Retail-AI--main`; không dùng
  dataset của bản mới trên Desktop.
- File `data/winmart_products.json` giữ nguyên SHA256
  `a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`.
  3.099 dòng, 3.081 sản phẩm hợp lệ và 18 dòng bị cách ly.
- Gọi HTTP thật `/sessions` → `/agent/respond` → đóng phiên. Dùng dịch vụ
  LLM thật, không mock, không đi qua STT trong bộ test tư vấn này.
- 35 câu của người dùng, mỗi câu một phiên mới; thêm 7 hội thoại, mỗi hội
  thoại 3 lượt liên tiếp: lẩu, mụn, dầu gội, nước uống, bữa tối, dọn nhà và
  câu có lỗi chính tả. Tổng 42 tình huống / 56 lượt hỏi.
- Các câu độc lập như “hai loại này”, “cái màu xanh” không có ảnh hay tên hàng
  trong phiên: hỏi một câu nhận diện là hành vi đúng. Không coi tự đoán SKU
  hoặc nhớ một lần mua không có trong phiên là tư vấn tốt.
- Bộ 35 câu đã được dùng trong quá trình sửa; đây là hồi quy trên tập phát
  triển, không phải phép đo độc lập khả năng tổng quát. Bộ 8 câu bổ sung chỉ
  kiểm tra mở rộng, không thay thế đánh giá với khách thật.
- Response ghi tên model thực sự trả lời. Có lượt dùng Qwen 3.8 27B, có lượt
  chuyển GPT-OSS khi dịch vụ giới hạn quota; không gộp thành một model Qwen.
- Sau bộ 56 lượt, chạy thêm 26 lượt tập trung vào ca khó và 8 câu mở rộng.
  Bản nguồn cuối bổ sung nới phần đuôi truy vấn, đã qua kiểm tra code; sau khi
  khởi động lại backend chỉ chạy thêm **hai câu HTTP**, chưa chạy lại đủ 56 lượt.
  Không gọi file có chữ `release` là kết quả của toàn bộ nguồn cuối.

## Nguyên nhân đã tìm thấy

1. **Luồng tra cứu chặn trước LLM.** Nhiều nhu cầu bị coi thành tên mặt hàng
   nguyên câu; không tìm trúng thì trả “không thấy trong danh mục”. Bản đầu
   gợi snack mực cho bữa sáng/cả tuần, và sau câu “tư vấn cho t” về mụn lại
   đưa đường và viên lẩu. Đây là lỗi hiểu ngữ cảnh, không phải không có hàng.
2. **Câu hỏi thất bại bị mất khỏi lịch sử.** Backend chỉ lưu lời khách sau khi
   gọi LLM thành công. Quota/lỗi sinh câu làm mất luôn yêu cầu vừa đổi; câu
   tiếp theo không còn biết ngân sách hoặc loại hàng. Đã lưu lời khách trước
   khi gọi, tránh nhân đôi lần thử lại của cùng câu chưa được trả lời.
3. **Lọc giá sau giới hạn kết quả.** Hàng rẻ phù hợp có thể bị loại khỏi nhóm
   ứng viên trước khi áp ngân sách. Nay lọc giá trong truy xuất, giữ lại thêm
   ứng viên bán theo kg để có thể mua khối lượng nhỏ hơn một kg.
4. **Giá/kg bị coi như giá một gói.** Nay mã chọn hàng cho phép chỉ rõ gram,
   backend tính tiền bằng số nguyên; không cộng một giá/kg vào giỏ như thể
   đó là tiền của 500g. Kiểm tra tổng giỏ và ngân sách chặt trước khi trả lời.
5. **Bỏ sót ngân sách hoặc quên ở lượt sau.** LLM giữ kế hoạch có cấu trúc,
   kết hợp kiểm tra mức tiền ghi rõ “dưới/tầm/khoảng/tổng/ngân sách” trong
   code; không phụ thuộc hoàn toàn vào việc model nhớ đúng một con số.
6. **Bỏ dấu làm sai phủ định trong kiểm tra công dụng.** “Chứa” và “chưa” bị
   chuẩn hóa thành cùng một từ, khiến câu “chứa ... dịu nhẹ” lọt kiểm tra như
   thể có lời phủ định. Đã đọc phủ định từ tiếng Việt nguyên dấu và thêm hồi quy.
7. **Model chép tên/giá thay vì dùng mã hàng.** Nay ánh xạ tên đầy đủ có đúng
   dấu/tokens về ID thật; tên gần giống không được tự map sang SKU khác.
   Giá/số lượng/tổng tiền do code in; loại giá ngoặc và dòng tổng bị lặp.
8. **Thiếu cơ chế hỏi khi chưa định danh.** Planner có quyết định hỏi/tư vấn.
   Khi cần nhận diện mặt hàng, trả một câu hỏi ngắn; không chạy tiếp bước
   chọn hàng để tự bịa màu vỏ hộp hoặc lần mua trước của khách.
9. **Truy vấn bị nhét mục đích/tính từ, làm mất hàng thực sự có.** Khi không
   tìm thấy, thử bỏ có giới hạn phần đuôi truy vấn, giữ hãng và ngân sách;
   sau đó mới tìm trong nhóm và xếp hạng theo từ liên quan. Có kiểm tra hồi
   quy cho thịt bò, dầu gội, sữa tắm. Đây là sửa truy xuất, không sửa lời STT
   và chưa phải bộ kiểm chứng đầy đủ loại hàng hay mục đích sử dụng.

## Cách tư vấn hiện tại

LLM đọc câu hiện tại, các lượt liên quan và kế hoạch trước để hiểu nhu cầu,
phủ định, chủ đề và ngân sách; sinh một số truy vấn loại hàng ngắn. Sau truy
xuất, LLM chọn hàng và viết lời tư vấn tự nhiên. Nếu thiếu thông tin quyết định,
nó hỏi lại. Giá, ID, số gói, khối lượng và phép cộng do code kiểm tra và in ra.

Kiến thức chung vẫn được dùng để gợi món ăn, cách chọn và cách phối hợp đồ.
Thông số riêng của SKU, so sánh hiệu quả, độ an toàn, trị bệnh, vị trí kệ hoặc
tồn hiện tại phải có bằng chứng tương ứng. Câu trả lời trước của bot không
được coi là nguồn chứng minh công dụng. Không thêm cảnh báo catalog/snapshot
vào mọi lượt tư vấn. Câu hỏi giá cụ thể, thành phần, khuyến mãi, hạn dùng hoặc
độ cay đơn lẻ còn dùng đường đọc dữ kiện. Câu hỏi tồn/vị trí đã chuyển sang
luồng tư vấn; thử thật vẫn thấy model tự nói vị trí chung trong cửa hàng.
Guard hiện tại chưa chặn hết biến thể câu này, nên chưa đạt yêu cầu về kệ.

Đã thử thêm bước reviewer LLM nhưng không giữ: reviewer vẫn để lọt khẳng
định sai, tăng thời gian và quota. Tăng reasoning ở một probe cũng vẫn gán
độ ngọt cho snack từ vị; không coi “bật reasoning” là đã giải quyết chất lượng.
Không đổi STT hoặc sửa transcript để che lỗi tư vấn.

## Tiêu chí đọc từng câu

Chấm nội bộ 56 lượt checkpoint `release`, 26 lượt xác minh, 8 lượt mở rộng
và hai lượt kiểm tra nguồn cuối là **đạt**, **một phần** hoặc **không đạt**,
ghi lý do cụ thể. Hai lần đo đầu chỉ tổng hợp hoạt động HTTP và dữ liệu,
không đặt thêm điểm chất lượng khi chưa chấm từng câu trong cùng bản đánh giá.

- Hiểu đúng nhu cầu và câu tiếp; giữ phủ định, ngân sách, dạng/khối lượng.
- Chọn hàng đúng loại và mục đích; hỏi lại khi thiếu định danh quan trọng.
- Không bịa tính năng, dinh dưỡng, điều trị, tồn/kệ hoặc lịch sử mua.
- Tư vấn có lý do/cách chọn hữu ích; không chỉ đọc tên hàng hoặc mời lọc hãng.
- Câu gọn, tự nhiên, không có log, không lặp tên/giá/tổng hoặc hỏi lại vô ích.

“Đạt” ở đây là chấp nhận được cho lượt test đó theo chấm nội bộ của trợ lý,
không phải nghiên cứu có người dùng độc lập chấm hay chứng nhận sản phẩm. HTTP lỗi tính là
không đạt. Một câu đúng giá nhưng sai tiêu chí vẫn không đạt.

## Nghiên cứu đã đối chiếu và phần áp dụng

| Nguồn | Điều rút ra cho dự án | Giới hạn của lần triển khai |
| --- | --- | --- |
| [CHIQ, EMNLP 2024](https://aclanthology.org/2024.emnlp-main.135/) | Làm rõ tham chiếu và lịch sử trước khi viết truy vấn; không dùng nguyên câu mơ hồ tìm hàng | Dùng planner và bộ nhớ có cấu trúc; không tái huấn luyện CHIQ |
| [ICR, EMNLP 2025](https://aclanthology.org/2025.emnlp-main.496/) | Hỏi/làm rõ có điểm dừng, tránh vừa đoán sớm vừa hỏi quá nhiều | Chỉ hỏi chi tiết cản trở lựa chọn; không triển khai SFT/DPO hoặc PRRF của paper |
| [Learning Multi-Step Query Rewriting via Corpus Feedback, 10/2026](https://arxiv.org/abs/2610.10955) | Truy vấn nên được điều chỉnh dựa trên kết quả thật, không chỉ ý tưởng trước truy xuất | Có backoff trong cùng nhóm, giữ giới hạn giá/hãng và xếp lại bằng từ liên quan; chưa có policy RL hay vòng tìm kiếm học được |
| [INTEGER, 10/2026](https://arxiv.org/abs/2610.08136) | Quyết định lúc hỏi/lúc gợi ý và dùng phản hồi sửa lựa chọn; gắn item ID vào lịch sử | Dùng ý tưởng giữ ngữ cảnh và ràng buộc ID; không có lịch sử hành vi thật, token học được hoặc generative recommender đã train |
| [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs) | Schema giúp đầu ra máy đọc được, không đảm bảo ý nghĩa đúng | Plan dùng schema; code kiểm tra giá/ID; vẫn cần đọc câu trả lời và đánh giá chất lượng |

Các nghiên cứu này chủ yếu đo tìm kiếm/hệ gợi ý, không chứng minh giao tiếp
bán hàng tiếng Việt của demo đạt mức nhân viên. Không tuyên bố phương pháp
“mạnh nhất” hoặc lấy số liệu paper làm số liệu dự án.

## Kết quả HTTP và chất lượng câu trả lời

| Lần đo | Lượt | HTTP 200 / lỗi | Median / p95 khi HTTP 200 | Đạt / một phần / không đạt |
| --- | ---: | ---: | --- | --- |
| Trước sửa | 56 | 56 / 0 | 0,178 / 30,844 giây | Chưa chấm từng câu trong bản đánh giá này |
| Checkpoint `verified` | 56 | 54 / 2 | 2,256 / 5,384 giây | Chưa chấm từng câu trong bản đánh giá này |
| Checkpoint `release` | 56 | 50 / 6 | 2,254 / 4,860 giây | 20 / 15 / 21 |
| Xác minh ca khó sau sửa | 26 | 22 / 4 | 3,002 / 5,142 giây | 2 / 7 / 17 |
| Tám câu mở rộng | 8 | 6 / 2 | 2,806 / 5,170 giây | 1 / 2 / 5 |
| Nguồn cuối: sữa tắm da nhạy cảm | 1 | 0 / 1 | Không có câu trả lời | 0 / 0 / 1 |
| Nguồn cuối: băng vệ sinh ban đêm | 1 | 1 / 0 | 2,909 giây; chỉ một mẫu | 0 / 1 / 0 |

Lỗi HTTP trong các lần đo này là 502. Không tính thời gian các lượt lỗi vào
median/p95 trên; đó không phải thời gian người dùng phải chờ của toàn bộ lượt.
Bản trước sửa có 28/56 lượt trả từ `catalog` và 4 lượt `local-policy`, nên
median thấp không có nghĩa hiểu ý hoặc tư vấn tốt hơn. Hai lần đo đầu còn
nhiều lỗi nội dung thể hiện trong câu trả lời nguyên văn.

Các tập khác nhau về độ khó và model, không dùng tỷ lệ trên để tuyên bố
chất lượng tăng/giảm theo phần trăm. Trong 56 lượt `release`, nhiều câu đạt
là hỏi lại đúng ở tình huống thiếu định danh; điều đó chưa chứng minh khả
năng chốt hàng, so sánh hoặc lập giỏ phức tạp. Không gộp các lượt lặp lại
thành số lượng khách hay tình huống độc lập.

Model ghi trong response là model viết câu cuối, không đại diện cho mọi
bước gọi LLM. Phân bố HTTP 200 của checkpoint `release`: Qwen 31,
GPT-OSS-120B 17, GPT-OSS-20B 1, catalog 1. Bộ 26 lượt: Qwen 8,
GPT-OSS-120B 9, GPT-OSS-20B 5. Sáu câu có phản hồi của bộ mở rộng đều dùng
GPT-OSS-120B. Có biến thiên theo model và giữa các lần gọi.

### Những ca đã cải thiện

- Theo câu hỏi mụn, “tư vấn cho t với” vẫn giữ chủ đề chăm sóc da; không còn
  nhảy sang đường hay viên lẩu trong bộ 26 lượt. Tuy vậy công dụng và cách
  tư vấn trong nhóm này vẫn còn lỗi, không chấm cả hội thoại là đạt.
- Câu “thịt bò bình thường hoặc ba chỉ, khoảng 200 nghìn” có lượt chọn khay
  ba chỉ bò 500g giá 165.000đ, không đòi tên hàng phải ghi “ăn lẩu”.
- “Nhà có gạo rồi” bỏ gạo ở lượt tiếp và giữ ngân sách. Giỏ thịt/rau/xốt
  được tính đúng 117.220đ trong bộ 26 lượt.
- “Sữa hộp màu xanh” trong bộ 26 lượt hỏi loại sữa, không còn gợi trứng gà.
- Lượt HTTP cuối về băng vệ sinh chọn Kotex ban đêm 35cm giá 21.500đ,
  đúng loại, độ dài và ngân sách. Còn lặp tên/giá; một lượt chưa đủ kết luận
  đã sửa ổn định mọi trường hợp. Probe trực tiếp trước đó dùng planner khác
  với lần lỗi, nên không quy toàn bộ cải thiện cho một thay đổi truy xuất.

### Những ca chưa đạt và lý do

| Nhóm | Bằng chứng từ lượt thật | Vấn đề cần giải quyết |
| --- | --- | --- |
| Chọn đúng loại | Bộ mở rộng h06 gợi **bông tắm dài** cho băng vệ sinh ban đêm; h01 gợi **cá viên** cho canh chua cá | Có ID/giá thật vẫn có thể sai mục đích. Cần xác minh loại hàng và tiêu chí trước khi chọn |
| Hàng có nhưng không tìm được | q20 của bộ 26 nói chưa có sữa tắm phù hợp dù nguồn có Dove chăm sóc da nhạy cảm | Truy vấn không được ràng buộc bằng mọi tính từ/công dụng. Bản cuối đã thêm backoff nhưng lượt HTTP q20 cuối lỗi, chưa xác minh lại được chất lượng |
| Giữ ràng buộc | drinks:3 đổi yêu cầu một chai 1 lít thành hai chai 500ml; shampoo:3 đổi 650g thành 380g | Tổng thể tích/tổng giá không thay thế yêu cầu dạng đóng gói hoặc kích cỡ tương tự |
| Công dụng | Gán X-men/Pantene hợp tóc dầu; Fuzetea ít đường; bánh vị rau giảm ngọt/dễ tiêu | Không suy công dụng riêng từ tên hãng hay hương vị. Guard regex hiện tại chưa đủ |
| Trẻ nhỏ/chăm sóc da | Chưa biết tuổi vẫn nói đồ ăn hợp trẻ; gợi Selsun dùng hằng ngày; gán Biore ngăn mụn mới | Cần đúng hướng dẫn nhãn, tuổi/cơ địa khi quyết định, và phân biệt chăm sóc với điều trị |
| Gia dụng | Gợi Sunlight trà xanh để rửa bình sữa; suy “thiên nhiên” giúp hạn chế kích ứng cho mèo | Dữ liệu hiện chưa chứng minh mục đích/an toàn đó |
| Vị trí hàng | Nói nước giặt gần nước xả/bột giặt, khăn giấy gần giấy vệ sinh | Không có sơ đồ kệ thật để chứng minh vị trí. Đổi route chưa chặn hết lời tự suy |
| Bữa ăn | Nói vài gói mì, cá hộp, bơ đủ cả tuần; thịt 375–500g đủ bốn người làm món chính | Cần hiểu số người, số bữa, khẩu phần; không hứa đủ lượng khi chưa rõ |
| Cách nói | Lặp tên rút gọn + tên đầy đủ + giá; có bảng/công thức dài, lời mời mua thêm không cần | Parser kiểm số liệu chưa đảm bảo lời nói gọn và tự nhiên |

Đây là đánh giá nội bộ của trợ lý trên câu trả lời được giữ nguyên, chưa có
người dùng độc lập chấm mù hoặc thử nghiệm nhân viên đối chứng. File đánh giá
ghi lý do riêng từng lượt; không dùng điểm do LLM tự chấm để chứng nhận đạt.

## Giới hạn dịch vụ đã xác minh

Probe bằng request planner thực tế trên bốn credential cấu hình đều nhận
**HTTP 429, TPD (tokens per day)**. Câu chào rất ngắn có thể vẫn trả 200;
không thể lấy phép thử câu chào làm bằng chứng đủ hạn mức cho tư vấn.
Đã có xoay credential được cấu hình khi 429, cooldown theo model/credential
và đọc Retry-After; việc này không tạo thêm quota và không bảo đảm các
credential có hạn mức độc lập.

Log an toàn của hai lượt kiểm tra cuối còn ghi `json_validate_failed`
bên cạnh `rate_limit_exceeded`. Không quy mọi 502 cho quota: schema có thể
thất bại, hoặc câu sinh ra bị kiểm tra nội dung từ chối. Chưa có trace nối
mọi lỗi của bộ 56 lượt đến từng request provider, nên không đặt số lượng
nguyên nhân riêng cho từng 502 từ suy đoán. Hai lượt cuối có tổng hợp mã lỗi
an toàn, không có raw error body hay credential.

Hạn mức giải thích gián đoạn và một phần việc chuyển model. Nó **không**
giải thích hết các câu HTTP 200 chọn sai hàng hoặc tự gán công dụng.

## STT: kết luận đến đâu

Đã sửa lỗi tự mở lại mic giữa câu khi quét thiết bị trả muộn; bài hồi quy
tái hiện được việc bộ thu mở hai lần trước sửa và giữ một lần sau sửa.
Chưa có bằng chứng lỗi này gây riêng cặp “lẩu → lấu”.

Sáu file qua HTTP thật có đối chiếu hash và phản hồi STT gốc: bốn bản “ăn
lẩu” đều đúng; hai bản “nước lau sàn” bị dịch vụ trả sàng/sang từ trước API.
Code giữ nguyên chữ đó qua API và giao diện trong các bài kiểm tra tương ứng.
Đó là bằng chứng về mẫu đã đo, không phải kết luận mọi lỗi ngoài thực tế do
model. Chưa có âm thanh của đúng lượt “lẩu → lấu” người dùng gặp.

Demo giữ `whisper-large-v3`, tiếng Việt, temperature 0. Không hardcode đổi
“lấu” thành “lẩu”. Trace phát triển đã bật để lưu đúng WAV nhận/gửi, hash,
phản hồi gốc và kết quả API theo cùng mã lượt; chi tiết thời hạn lưu nằm
trong báo cáo STT. Chưa thử lại mic vật lý/tiếng quạt trong phòng người dùng.

## Xác minh code và bước còn thiếu

- **626/626** bài backend qua trên nguồn cuối, không lỗi/bỏ qua. Bộ này dùng
  repository demo theo thiết lập fixture, không thay thế kiểm tra snapshot thật.
- **45/45** bài frontend qua, gồm hai hồi quy mic và ba hồi quy giữ nguyên dấu
  từ STT đến dòng lời khách và text gửi backend. Build/typecheck đã qua;
  còn cảnh báo kích thước phần hiển thị avatar.
- **70/70** kiểm tra catalog HTTP thật qua; file sản phẩm cũ giữ nguyên hash.
- Ruff các phần tư vấn, dịch vụ, trace, chương trình đánh giá và bài hồi quy
  liên quan qua. Giá/ID/phép cộng là kiểm tra code; không đồng nghĩa tư vấn đạt.
- Chưa chạy lại đủ 56 lượt trên nguồn cuối sau backoff. Hai lượt kiểm tra
  cuối gồm một lượt 502 và một lượt đúng loại hàng nhưng còn lặp lời.

Ưu tiên tiếp theo là xác minh loại hàng và ràng buộc từ kế hoạch tới lựa
chọn; bổ sung dữ kiện nhãn có nguồn cho công dụng/cách dùng; giữ kế hoạch
có cấu trúc cho dạng đóng gói và phủ định qua các lượt. Cần tách kiểm tra
giá/ID khỏi đánh giá ý nghĩa, thay vì tiếp tục chỉ nối thêm regex/prompt.
Hạn mức và lỗi schema cũng cần ổn định trước khi đánh giá lặp lại trên cùng
model, cùng tập giữ riêng. Tiêu chí đạt phải gồm chọn đúng loại, không tự
gán công dụng, giữ ngữ cảnh và nói gọn; chưa đạt các điều này thì chưa gọi
là “nhân viên thật”.

## Bằng chứng và tái lập

- [56 lượt trước sửa](artifacts/consultation_text_before_20261110.json).
- [Checkpoint 56 lượt `verified`](artifacts/consultation_text_verified_20261110.json).
  Tên file là tên lần đo, không có nghĩa toàn bộ câu tư vấn đã đạt.
- [Checkpoint 56 lượt `release`](artifacts/consultation_text_release_20261110.json).
- [26 lượt xác minh ca khó](artifacts/consultation_text_patch_validation_20261110.json).
- [Tám câu mở rộng](artifacts/consultation_text_expansion_20261110.json).
- [Nguồn cuối: sữa tắm](artifacts/consultation_text_final_skin_smoke_20261110.json),
  [băng vệ sinh](artifacts/consultation_text_final_type_smoke_20261110.json),
  [hash nguồn và mã lỗi dịch vụ](artifacts/consultation_final_runtime_manifest_20261110.json).
- [Đánh giá từng lượt](artifacts/consultation_qualitative_reviews_20261110.json)
  và [bảng tổng hợp](artifacts/consultation_quality_summary_20261110.json).
- [TPD từ request planner thực tế](artifacts/provider_planner_limit_dimensions_20261110.json).
- [626 bài backend](artifacts/backend_verified_20261110.xml),
  [45 bài frontend](artifacts/stt_frontend_final_tests_20261110.json),
  [70 kiểm tra catalog](artifacts/catalog_runtime_release_20261110.json).
- [Báo cáo STT và dấu tiếng Việt](stt_tones_code_audit_20261110.md).

Chạy text thật với backend đang mở, tách khỏi STT:

```powershell
.venv\Scripts\python.exe -X utf8 evaluation/audit_live_consultation.py --ids q20 --pause-seconds 12 --output tmp/consultation-text-check.json
```

Đây là lời gọi dịch vụ thật và tốn quota. Để tổng hợp artifact có sẵn, dùng
`evaluation/summarize_consultation.py` với `--reviews` trỏ tới file đánh giá
và `--output` trỏ tới file tổng hợp; chương trình đó không gọi LLM.
