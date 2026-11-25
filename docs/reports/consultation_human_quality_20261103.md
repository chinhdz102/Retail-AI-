# Kiểm tra tư vấn trên hàng thật và đánh giá chất lượng — 03/11/2026

**Kết luận: chưa đạt mức nhân viên bán hàng thực sự.** Đã sửa các lỗi tìm nhóm
hàng, giữ tiêu chí và xử lý thất bại có thể tái hiện. Kiểm tra ràng buộc trên toàn
danh mục đạt, nhưng khả năng chủ động tư vấn bằng LLM chưa ổn định. Lượt chạy thật
vẫn hỏi thừa; nhiều lượt không tư vấn được vì provider giới hạn quota. Không lấy
HTTP 200, số thẻ sản phẩm hoặc số test qua làm điểm giống nhân viên.

## Phạm vi và nguồn dữ liệu

- Danh mục Thanh Lam Mart đang dùng: **3.081 sản phẩm, 346 đường dẫn nhóm hàng, 344 nhãn
  nhóm cuối**, không trùng product ID. SHA256 snapshot:
  `a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`.
- Đây là snapshot được dự án lưu, không phải tồn kho hay giá thời gian thực tại
  quầy. Toàn bộ 3.081 bản ghi tồn đều không có xác nhận hiện tại. Giá và đơn vị bán
  được ràng buộc bằng dữ liệu; không suy giá một kg thành giá một gói.
- Kiểm tra toàn danh mục về phạm vi nhóm hàng, giới hạn giá, loại trừ, trùng kết
  quả và giữ ràng buộc khi hỏi tiếp. Đây là kiểm tra truy xuất, không phải 3.081
  cuộc trò chuyện đã được người dùng chấm.
- Chạy **35 câu người dùng đã cung cấp + 19 lượt trong các journey**, tổng **54
  lượt SSE**, trước các sửa cuối. Giữ nguyên artifact và hash của lần chạy đó.
- Chạy lại **8 lượt / 3 journey** lẩu, da mụn, dọn nhà sau sửa định tuyến/facet.
  Sửa nhỏ về nhận diện quy cách gói được bổ sung sau lượt này và có kiểm thử riêng;
  không đổi hash artifact cũ thành kết quả của source mới.
- Bộ 35 câu đã được dùng phát triển từ trước, không phải tập kiểm tra mù. Những
  tình huống bổ sung trong đợt này cũng là development data. Chưa có nhân viên
  bán hàng độc lập hoặc khách thật tham gia chấm.

## Lỗi thật tìm được và thay đổi

| Vấn đề | Nguyên nhân | Thay đổi và bằng chứng |
| --- | --- | --- |
| Gối ngủ không hiện trong ví dụ hàng đưa cho planner | Bỏ dấu làm gối/gói/gội trùng nhau; từ đệm và số tiền ảnh hưởng xếp hạng | Giữ dấu khi có dấu; vẫn hỗ trợ câu không dấu; tăng trọng số cụm nhóm hàng đầy đủ. Gối từ hạng 15 lên hạng 1, có test với danh mục thật và các ngành tổng hợp khác. |
| Nhãn nhóm gây hiểu nhầm dạng hàng | Planner nhìn nhãn rộng, không thấy tên hàng bên trong | Bổ sung tối đa 2 ví dụ tên thật mỗi nhóm liên quan; chọn theo đa dạng từ vựng từ catalog hiện hành, không có bảng câu hỏi → SKU. Ví dụ chỉ là quan sát, không là lời giới thiệu sẵn. |
| Nhóm cuối trùng tên với nhóm cha làm mở rộng truy xuất | Resolver dùng từ trùng/nhãn cha thay vì phạm vi được yêu cầu | Ưu tiên đường dẫn chuẩn khớp chính xác và nhãn nhóm cuối khớp chính xác trước mở rộng alias; test thêm nhánh trùng tên. |
| Hỏi tiếp làm mất yêu cầu loại trừ hoặc yêu cầu từ khóa | Plan mới bỏ các trường danh sách | Giữ ràng buộc theo đúng nhóm/vai trò khi hỏi tiếp; không chép loại trừ của món này sang mọi món trong giỏ. Khi khách nhắc lại tiêu chí để đổi, planner được cập nhật tiêu chí đó. |
| Tìm không thấy rồi sửa kế hoạch làm mất yêu cầu bắt buộc | Nhánh repair giữ giá/loại trừ nhưng không giữ required_all/any | Không đổi phạm vi tự động ở nhánh repair này khi có yêu cầu từ khóa bắt buộc theo vai trò. Vẫn có thể trả hướng tư vấn chung được duyệt, không ép chọn hàng sai. |
| “bé 10kg” bị biến thành gói 10.000g | Mọi số kèm đơn vị đều bị ép vào required_size | Phân vai bằng kế hoạch ngữ nghĩa rồi đối chiếu số đo trong lời khách; chỉ nhận thêm quy cách từ ngữ pháp gói/chai/hộp hoặc dung tích rõ. Có test cân nặng người/trẻ/thú cưng, lượng dùng mỗi ngày, số thập phân và lượt thử lại. Không khẳng định mọi mô hình sẽ luôn phân vai đúng. |
| Planner trả null ở trường văn bản không dùng, hỏng cả lượt | Wire schema chỉ nhận chuỗi rỗng | Chỉ cho phép null ở trường vốn có mặc định chuỗi rỗng và chuẩn hóa về chuỗi rỗng. Tên nhóm bắt buộc, danh tính, giá/số lượng và ràng buộc số vẫn kiểm tra chặt. |
| Reviewer trả critique quá 500 ký tự, Groq trả 400 | Giới hạn metadata quá ngắn dù nội dung kiểm tra có thể hợp lệ | Đã tái hiện bằng đúng một request reviewer riêng; trường `issue` báo string_too_long. Nới riêng issue lên 1.200 ký tự, đồng thời yêu cầu critique ngắn. Không nới độ dài tên hàng hay giới hạn tiền. Cần replay thành công khi quota cho phép để xác nhận ổn định. |
| Lỗi provider làm lộ lời khuyên chưa duyệt | Nhánh fallback dùng guidance từ planner | Guidance chỉ được xuất qua bước duyệt độc lập; khi lỗi provider, giữ lời khách/ràng buộc và không công bố sản phẩm hoặc hướng dẫn chưa duyệt. Có test reviewer bác lời khuyên. |
| Dọn nhà hỏi tiếp trả 502; chuyển nước rửa chén dưới 50k lại hiện hàng 116.200đ | Lỗi ngay bước planner chưa tạo need, chỉ có unprocessed_request; nhánh sau không xem đây là phiên tư vấn đang chờ | API giữ unprocessed_request làm ngữ cảnh tư vấn đang chờ qua lần lỗi tiếp theo. Test SSE bằng snapshot thật cho dọn nhà, lẩu và chuyển nhóm; test đối chứng tra giá/vị trí vẫn đi đúng controller. |
| Retry quá sớm khi provider yêu cầu đợi lâu | Cooldown trước đây chặn tối đa 300 giây | Tôn trọng Retry-After dài hơn, giới hạn tối đa một ngày; test 754 giây vẫn cooldown ở giây 301 và thử lại sau hết hạn. Không ngủ chặn request. |

Policy đã được rút gọn và tách rõ mục tiêu hội thoại, chọn hàng và duyệt. Cho phép
kiến thức mua sắm thông thường để đưa hướng chọn/đánh đổi; tên hàng, giá, định
lượng và phát biểu riêng về SKU phải có cơ sở. Không cài câu trả lời riêng cho 35
câu hoặc dùng bảng ánh xạ món ăn → sản phẩm.

Bổ sung nguồn hướng dẫn chung về chất giặt HE (P&G), chăm sóc tóc (AAD), làm sạch
nhà (CDC), bên cạnh 5 nguồn về thịt, mụn, trẻ nhỏ, thú cưng và đường đã có. Đây là
ngữ cảnh cho mô hình suy luận, không phải chứng nhận tính năng cho một SKU và
không phải đáp án viết sẵn. Ví dụ không được suy “mọi nước giặt đều hợp cửa trước”,
không mặc định cần mua cả bộ khử khuẩn để dọn nhà, không gợi gel tạo kiểu khi khách
đang cần dầu gội.

## Kết quả kiểm tra toàn danh mục

[Artifact source cuối](artifacts/consultation_catalog_constraints_delivery_20261103.json)
ghi nguồn, hash, danh sách kiểm tra và kết quả theo từng đường dẫn.

**12.357 kiểm tra qua, 0 lỗi**:

| Kiểm tra | Số lượng qua |
| --- | ---: |
| Mỗi sản phẩm: loại trừ nguyên tên | 3.081 |
| Mỗi sản phẩm: giá bằng trần bao gồm | 3.081 |
| Mỗi sản phẩm: giá bằng trần nghiêm ngặt bị loại | 3.081 |
| Đường dẫn chuẩn: không lạc phạm vi và có kết quả | 346 + 346 |
| Nhãn nhóm cuối: không lạc phạm vi và có kết quả | 346 + 346 |
| Giới hạn giá nghiêm ngặt trên kết quả truy xuất | 346 |
| Loại trừ trên kết quả truy xuất | 346 |
| Hai lần tìm cùng nhóm không sinh kết quả trùng | 346 |
| Hỏi tiếp giữ giá và loại trừ | 346 + 346 |

Giới hạn: 326 đường dẫn đủ ngắn để đi qua schema category 100 ký tự; 20 đường dẫn
dài hơn chỉ được kiểm tra trực tiếp ở helper, có đánh dấu bypass trong artifact.
Truy xuất lấy mẫu tối đa 8 hàng mỗi search; số trên không đo recall mọi SKU trong
câu tư vấn. Nhóm trùng nhãn cuối vẫn cần ngữ cảnh đường dẫn để phân biệt.

## Đọc và đánh giá lượt trả lời thật

### Bộ 54 lượt trước các sửa cuối

[Transcript đầy đủ](artifacts/eval_subagent_full35_20261103.json).
Mô hình/credential bị rate-limit ngay lượt tư vấn đầu, các lượt sau phần lớn
được bỏ qua theo cooldown. **36 lượt consultation fallback, 10 lượt hỏi danh tính,
6 lượt controller catalog, 1 local clarification và 1 HTTP 502.**

Riêng 35 câu: **22 fallback, 10 hỏi danh tính sản phẩm, 3 controller catalog**.
Hỏi danh tính hợp lý ở một lượt không có nghĩa đã hoàn thành tư vấn. Không gọi
toàn bộ các câu còn lại là đạt chất lượng nhân viên.

[Nhận xét từng câu và rubric đầy đủ](consultation_human_manual_review_20261103.md)
đi kèm [62 nhận xét có hash nguồn](artifacts/consultation_human_manual_review_20261103.json).

Transcript đã bắt được lỗi phục vụ thật: hàng vượt giá trong nhánh chuyển nước
rửa chén; thịt bò xay/giá kg không phù hợp hướng nhúng; và 502 ở lượt dọn bếp.
Các lỗi rơi nhánh khi outage hiện có regression đã qua. Artifact trước sửa vẫn
giữ nguyên để thấy lỗi, không sửa nội dung để tạo kết quả đẹp.

### Lượt chạy lại 8 lượt

[Transcript](artifacts/consultation_human_final_live_20261103.json).
**8/8 HTTP 200; 6/8 fallback; 2 lượt hỏi thêm, không có sản phẩm.**

- Lẩu lượt đầu hỏi số người là có ích nếu tính lượng, nhưng chưa đưa hướng chọn
  và còn xưng “t” trong câu hỏi. Đánh giá **yếu**, không đạt giọng nhân viên.
- Khách hỏi tiếp “loại thịt nào để ăn lẩu”, hệ thống đáp: “Bạn muốn ưu tiên quy
  cách hay mức giá nào để mình tìm tiếp?”. Khách đang cần hướng chọn, câu hỏi này
  đẩy quyết định lại cho khách. Đánh giá **không đạt tư vấn** dù transport qua.
- Lượt khách nêu bò/ba chỉ và 200k: provider lỗi, không chọn được hàng. Sau sửa
  không còn lọt xuống nhánh trả thịt xay hoặc hàng không giữ được ngữ cảnh.
- Ba lượt da mụn và hai lượt dọn nhà đều fallback. Không có đủ đáp án thành công
  để đánh giá khả năng giải thích/chọn hàng. Dọn bếp hỏi tiếp đã trả SSE hoàn chỉnh,
  không còn 502 trong lần replay này.

Những draft đọc được trong diagnostic trước đó vẫn có suy diễn “đủ bữa tối” hoặc
hiểu giò sống thành thịt thái lát. Reviewer có thể bác đúng nhưng cũng có thể viết
quá dài hoặc hỏi ngược. Thêm reviewer không tự động bảo đảm chất lượng.

### Replay outage trên source bàn giao

[54 lượt offline bắt buộc provider lỗi](artifacts/consultation_final_forced_outage_offline_20261103.json)
dùng FastAPI TestClient và snapshot thật, chặn mạng ngoài và thay chat bằng lỗi
cố định. Hash nguồn đầu/cuối khớp. **54/54 HTTP 200 và SSE hoàn chỉnh**; cả **9 lượt
hỏi tiếp sau request đang chờ** trả thông báo lỗi có thể đọc, không lọt sản phẩm
qua nhánh tìm rộng. Hai câu tra vị trí #26/#29 vẫn trả thẻ catalog tương ứng.
Không có request mạng ngoài. Đây là xác nhận luồng outage ở source cuối, không
phải 54 câu tư vấn do LLM tạo thành công.

## Paper và dữ liệu đã đối chiếu

| Nguồn | Bài học áp dụng / hướng tiếp theo | Giới hạn |
| --- | --- | --- |
| [ChatCRS, NAACL 2025](https://aclanthology.org/2025.findings-naacl.17/) | Tách mục tiêu hội thoại với truy xuất tri thức; hướng dẫn phải giúp một quyết định thay vì chỉ đọc hàng. Giữ planner/selector riêng, đưa quan sát thật vào kế hoạch. | Paper có thành phần học planner; dự án hiện dùng prompting, không tái lập số cải thiện của paper. |
| [ProductAgent / PROCLARE, EMNLP Industry 2025](https://aclanthology.org/2025.emnlp-industry.25/) | Trí nhớ có cấu trúc, tóm tắt ứng viên, vòng tìm–hỏi–cập nhật. Áp dụng typed state, quan sát nhóm và repair giữ ràng buộc. | 2.000 phiên mô phỏng trong paper không chứng minh chất lượng tiếng Việt của kiosk. |
| [PC-CRS, EMNLP 2024](https://aclanthology.org/2024.findings-emnlp.247/) | Critique phải đối chiếu lời giải thích với item; sửa đúng bản nháp và phản hồi cụ thể. Giữ draft + feedback khi tạo lại, giới hạn số vòng. | Độ thuyết phục và độ tin cậy có đánh đổi; reviewer cũng có lỗi. |
| [PEPPER, EMNLP 2025](https://aclanthology.org/2025.findings-emnlp.1067/) | Không chấm CRS bằng trò đoán một target item hoặc recall một lượt; cần đo quá trình khách hình thành ưu tiên. Tách trải nghiệm hội thoại khỏi grounding. | Đánh giá phát triển hiện chưa triển khai simulator target-free hoặc human blind review của paper. |
| [SIGDIAL 2024: ambiguous references trong dịch vụ](https://aclanthology.org/2024.sigdial-1.25.pdf), [dữ liệu công bố](https://github.com/ygan/Chinese-Ambiguous-Reference) | Khoảng 1.000 hội thoại mua sắm thực tế giúp phân biệt hỏi trống với đưa lựa chọn/hướng trước khi hỏi. Câu hỏi cần giúp khách biết trả lời gì. | Dữ liệu tiếng Trung và ngữ cảnh thu thập riêng; không nhập làm lời khách Việt hoặc giả là giao dịch của cửa hàng. |
| [Ask or Recommend, CIKM 2024](https://www.dare.uva.nl/id/70b80505-4b22-4388-b918-57f94291aebe) | Hỏi thêm có thể giúp tìm hàng, nhưng quá nhiều câu hỏi và độ trễ làm giảm hài lòng. Cần đo hữu ích của câu hỏi, số lượt tới quyết định và latency. | Không suy từ nghiên cứu rằng mọi khách phải trả lời đủ bộ lọc hoặc luôn phải được chốt hàng ngay. |
| [Amazon Shopping Queries / ESCI](https://github.com/amazon-science/esci-data) | Phân biệt đúng món, thay thế, phụ trợ và không liên quan. Hợp để dựng nhãn hard-negative cho bài toán nguyên liệu/đồ có nhân và đồ chính/phụ kiện. | Dữ liệu Anh/Nhật/Tây Ban Nha; chưa huấn luyện reranker Việt hoặc nhập vào runtime. |
| [MG-ShopDial, SIGIR 2023](https://iai-group.github.io/MG-ShopDial/) | Hội thoại người–người có nhiều mục tiêu: hỏi thông tin, tìm, tư vấn và chuyển mục tiêu. Dùng làm khung thiết kế bộ test nhiều lượt. | Tập nhỏ 64 hội thoại tiếng Anh; không dùng số liệu đó làm kết quả hệ thống này. |

Nguồn thực hành bổ sung: [P&G về HE](https://pg-lex.my.salesforce-sites.com/CarehubStandalone/articles/en_US/FAQ/Popular-questions-about-HE-laundry-detergent),
[AAD về tóc](https://www.aad.org/public/everyday-care/hair-scalp-care/hair/healthy-hair-tips),
[CDC về làm sạch nhà](https://www.cdc.gov/hygiene/about/when-and-how-to-clean-and-disinfect-your-home.html).
Contract và quota được đối chiếu [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs)
và [Rate Limits](https://console.groq.com/docs/rate-limits); không coi strict JSON là
kiểm tra ngữ nghĩa và không coi quota là lỗi có thể chữa bằng câu trả lời viết sẵn.

## Tiêu chí nghiệm thu tiếp theo

Chấm mỗi lượt thành công theo 5 mục 0–2 điểm: đúng mục đích/chức năng hàng; có
lựa chọn và lý do hữu ích; giữ ngữ cảnh/ràng buộc; hỏi điều thiết yếu; lời nói tự
nhiên ngắn gọn. Giá/đơn vị/tổng và phát biểu hiệu quả phải có kiểm tra riêng.
Một lượt lạc chức năng, vi phạm ràng buộc hoặc bịa hiệu quả là fail bất kể tổng
điểm. Lỗi dịch vụ được đánh dấu unavailable, đồng thời tính là khách chưa được
phục vụ; không loại khỏi báo cáo để nâng điểm.

Ưu tiên công việc còn lại:

1. Có quota/provider ổn định để chạy đủ một phiên bản source cố định. Hiện cần
   3 bước LLM thông thường và có thể có repair; đo token/latency từng bước trước
   khi chọn số bước hoặc model. Không tự đổi sang dịch vụ tính phí khác.
2. Dựng tập query–product Việt có nhãn đúng/thay thế/phụ trợ/lạc nhóm từ catalog
   thật, gồm cả các món dễ nhầm trong cùng nhóm. Giữ một tập chưa dùng sửa prompt;
   đánh giá lexical + dense retrieval/reranker bằng ablation trước triển khai.
3. Thu thập hội thoại được phép sử dụng với nhân viên thật: nhờ chọn, không biết,
   sửa tiêu chí, ngắt lời, đổi nhóm và cần hướng dẫn chung khi SKU thiếu nhãn.
   Chấm mù hai người, đo bất đồng, không dùng chính 35 câu để tuyên bố tổng quát.
4. Chỉ sau khi có dữ liệu và baseline tốt mới so sánh SFT hoặc preference tuning
   cho cách hỏi/chọn. Chưa thực hiện huấn luyện trong đợt này.
5. Chặn cách hỏi ngược trong các ca đủ thông tin bằng quyết định hội thoại có
   kiểm chứng; không tăng số regex hoặc lời mẫu riêng cho thịt lẩu/da mụn.

## Kiểm thử phần mềm và demo

- [Scoped trên source cuối](artifacts/consultation_human_scoped_final_20261103.txt):
  **279 passed**, một warning Starlette/httpx.
- [Frontend](artifacts/consultation_human_frontend_20261103.txt): **102 passed / 9 file**.
  [Build](artifacts/consultation_human_build_20261103.txt) qua; còn warning chunk
  model-viewer lớn hơn 500 KB. Đây không phải kiểm tra âm thanh trên thiết bị thật.
- Ruff các file sửa và `git diff --check` qua. Dữ liệu/mã trước đó trong working
  tree được giữ nguyên, không gom thành một commit khó phân biệt.
- Lượt full đầu có một regression thật về thử lại quy cách 500ml; đã sửa ngữ
  pháp gói và chạy lại test đó cùng bộ scoped. Log đầu được giữ ở
  [backend_final](artifacts/consultation_human_backend_final_20261103.txt).
- Full regression xác nhận source cuối:
  **1.871 passed, 1 skipped, 1 warning**, 303,33 giây, tại
  [backend_verified](artifacts/consultation_human_backend_verified_20261103.txt).
  Skip do Windows không có quyền tạo symlink; warning là Starlette/httpx.

Demo development: [mở demo](http://127.0.0.1:5173/). Khởi động bằng
`scripts/start_demo.ps1` để bật consultation/review/demo sales; cờ mặc định vẫn
tắt ngoài demo. Đã seed đủ 3.081 số lượng bán demo, khoảng 10–999; nguồn mô phỏng
được giữ trong dữ liệu nội bộ. Không đánh đồng với lịch sử bán thực tế.

## Giọng nói

Đợt này sửa tư vấn và định tuyến dùng chung; frontend voice regression nằm trong
102 test đã qua. Không chạy lại nghiệm thu micro/loa phòng thật hoặc chấm người
nghe TTS. Kết quả đo audio trước nằm ở
[báo cáo voice](consultation_voice_completion_20261101.md).
**LiveKit chưa tích hợp runtime**; xem [readiness](livekit-turn-taking-readiness-20261101.md).
Không dùng test mô phỏng để kết luận STT/TTS/VAD/LiveKit đã “ngon” trên kiosk thật.
