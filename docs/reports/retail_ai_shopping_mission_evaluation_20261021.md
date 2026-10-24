# Đánh giá luồng lập kế hoạch mua sắm tiếng Việt — 21/10/2026

## Phạm vi

Luồng nhận mục tiêu mua sắm bằng ngôn ngữ tự nhiên, suy ra các nhóm hàng cần thiết, tra sản phẩm trong catalog, cập nhật ngân sách và giữ trạng thái kế hoạch qua nhiều lượt. Kế hoạch chỉ là gợi ý; không tự ghi vào giỏ.

## Dữ liệu và cách đo

- Bộ holdout tổng hợp có **100 tình huống, 300 lượt hội thoại**, chia thành 10 archetype: đón khách, du lịch, chăm sóc da, vệ sinh nhà, đồ cho em bé, chuẩn bị đi học, bữa sáng gia đình, họp văn phòng, chuyển nhà và picnic. Prompt không đưa tên SKU để tránh chấm điểm bằng khớp tên đã biết.
- Bộ dữ liệu: [vietnamese_shopping_mission_heldout_v1_20261021.json](../../evaluation/cases/vietnamese_shopping_mission_heldout_v1_20261021.json). SHA-256: `cecbcfbff4646b5f3095d062c621268ce660900a08ae66e27c8e87b87f62ea64`.
- Catalog runtime là snapshot WinMart gồm 3.099 mã sản phẩm, timestamp quan sát trong lượt chạy là `2026-09-15`. Đây không phải tồn kho cửa hàng trực tiếp; hệ thống phải nói rõ khi chưa xác minh tồn thực.
- So sánh `current` và `legacy` là **controlled ablation** trên cùng checkout, model, key và catalog; `legacy` chỉ ẩn route/tool lập kế hoạch. Đây không phải chạy lại commit lịch sử.
- Bộ chấm tự động kiểm tra kế hoạch, mục tiêu, nhóm hàng bắt buộc/tùy chọn, ngân sách, số người, SKU tồn tại trong catalog, tính liên tục mission và giỏ hàng không bị sửa. Bộ chấm không thay cho người đọc đánh giá câu trả lời.

## Kết quả chạy live

Chạy một tình huống đón bạn tại nhà (`SMH-001`), ba lượt, trên Groq; sau đó chạy cùng ba lượt ở chế độ đối chứng.

| Chế độ | Có kế hoạch | Lượt đạt toàn bộ 16 kiểm tra | Median API |
| --- | ---: | ---: | ---: |
| Planner hiện tại | 3/3 | 3/3 (48/48 kiểm tra) | 3.168 ms |
| Controlled ablation | 0/3 | 0/3 | 842 ms |

Ở chế độ hiện tại, cuộc hội thoại bắt đầu với 3 người và 80.000đ; lượt sau chốt đồ ăn nhẹ và đồ uống, coi “không cay” là sở thích chưa xác minh; lượt cuối giảm ngân sách còn 60.000đ và giữ số người/nhu cầu. Cả ba lượt giữ nguyên `mission_id`, chỉ trả SKU có trong catalog và không thay đổi giỏ. Ở ablation, hệ thống vẫn trả HTTP 200 và có thể tìm SKU riêng lẻ, nhưng không duy trì được kế hoạch nhiều lượt.

Median thời gian ở hai chế độ không phải phép so tốc độ ngang nhau: planner làm thêm việc trích nhu cầu, tra cứu nhiều nhóm hàng và tính lại tổng tiền. Mỗi lượt vẫn có độ trễ phụ thuộc model/provider.

## Lỗi phát hiện và sửa

1. Một câu giảm ngân sách kèm “giữ nguyên nhu cầu” bị bộ nhận diện truy vấn sản phẩm đưa sang catalog. Route đang mở nay giữ thao tác sửa ngân sách trong planner; câu hỏi giá sản phẩm cụ thể vẫn dùng tra cứu catalog.
2. “Ưu tiên món không cay” bị biến thành hard filter trên mọi nhu cầu, kể cả đồ uống không có thuộc tính cay. Nay cờ lọc cay được xóa khỏi tất cả nhóm; sở thích chỉ gắn vào món ăn và câu trả lời báo rõ khi catalog không có thuộc tính để xác minh.
3. Model đôi khi đánh dấu nhóm hàng người dùng vừa nêu là tùy chọn. Sanitizer nay giữ các nhóm được nêu thành nhu cầu tường minh/bắt buộc, phân biệt với gợi ý suy ra; lượt “giữ nhu cầu đã chốt” kế thừa nguồn và trạng thái bắt buộc cũ.
4. Cụm “xem phim” làm yêu cầu chỉ hỏi snack bị chuyển nhầm sang planner nhiều nhóm. Router nay ưu tiên tư vấn danh mục khi công cụ recommendation phù hợp, nhưng vẫn giữ planner cho mục tiêu nhiều nhóm và mục tiêu đang hoạt động.
5. “Hạ Coca xuống 1.000 đồng” bị hiểu thành hỏi giá. Bộ phát hiện thao tác nay chặn kiểu sửa giá có số tiền, đồng thời không chặn câu hạ ngân sách tổng.

## Kiểm thử

Các test liên quan được chạy lại sau chỉnh sửa: **421 passed** qua `test_retail_api.py`, `test_retail_agent.py`, các test shopping mission/routing/holdout, Groq STT và catalog transcription. Ruff và `git diff --check` đều qua.

## Giới hạn và phần còn lại

- Chạy đại diện bổ sung **10/100 tình huống, 30 lượt** ở chế độ current; chỉ 25/30 HTTP 200, 5 lượt trả 502 sau khi hết fallback. Có kế hoạch ở 14/30 lượt và chỉ 1/30 lượt qua toàn bộ grader đã siết. JSON giữ `partial=true`; đây là run chẩn đoán, không phải tỷ lệ chính xác tổng quát. Lượt này diễn ra trước khi sửa false-positive “không biết tên”.
- Groq gửi `RateLimitError` cho cả model/key fallback; các header cho thấy giới hạn 8.000 token/phút và `Retry-After` 7–36 giây. Một số lượt OSS/Qwen trả `BadRequestError`; chưa phân loại được nội dung 400, nên cần thu lỗi đã làm sạch trước khi kết luận về cấu hình model.
- Snapshot catalog không xác nhận tồn kho tại cửa hàng hôm nay, chất lượng sản phẩm, mức độ cay hay mức bán chạy nếu trường đó không có dữ liệu.
- Kiểm tra STT bằng audio tiếng Việt công khai và noise tổng hợp được báo riêng trong [báo cáo ổn định STT/TTS](retail_ai_voice_reliability_20261021.md). Ở lần kiểm tra browser mới, thiết bị AMD đã xuất hiện nhưng tín hiệu gần im lặng khi thử phát lại audio; chưa có lời nói thật đi vào mic để tính WER.
- Cần chạy toàn bộ 100 tình huống live và có người đánh giá tự nhiên, hữu ích, không bịa trước khi kết luận chất lượng tổng quát.

## Kết quả máy

- [Current và ablation, 1 tình huống / 3 lượt](../../evaluation/results/live_shopping_mission_benchmark_smoke_postfix4_both_20261021.json)
- [Current, 1 tình huống / 3 lượt](../../evaluation/results/live_shopping_mission_benchmark_smoke_postfix4_20261021.json)
- [Current, 10 tình huống / 30 lượt, partial và bị rate-limit](../../evaluation/results/live_shopping_mission_representative_current_postroute_20261021.json)

## Sửa định tuyến và bộ chấm — 21/10/2026

Kiểm tra 100 câu mở đầu product-free cho thấy 13 câu bị router bỏ lỡ; các lỗi tập trung ở cách nói tư vấn theo nhu cầu, dọn nhà/chuyển nhà, bữa sáng, đi cùng trẻ, học tập và tiếp khách. Đã khái quát cue theo ý định ưu tiên/chuẩn bị và hoạt động, đồng thời giữ câu hỏi một danh mục cụ thể ở route catalog. Mô phỏng router bằng cùng các bộ nhận diện mà API dùng cho 100 câu mở đầu: 100/100 đi vào planner; kiểm tra 300 lượt của holdout cho thấy 0 lượt bị xem nhầm là nhắc đến SKU cụ thể.

Benchmark 10 tình huống phát hiện thêm lỗi luồng: cụm “không biết tên món cần mua” bị hiểu nhầm là lời nhắc “không đoán”, nên agent trả lời từ chối dữ liệu và mất mission trước khi tư vấn. Đã bỏ các cụm “không biết tên/chưa nhớ tên” khỏi shortcut xác nhận không đoán; chỉ giữ chỉ dẫn rõ như “nếu không có dữ liệu thì nói rõ” và “đừng đoán”. Thêm kiểm thử API mô phỏng câu tư vấn không có tên mặt hàng.

Siết grader để kiểm tra nhóm hàng bắt buộc có mặt trong **phương án đầu tiên người dùng thấy**, số item trên chính phương án đó, phép tính dòng hàng/tổng tiền, nguồn và timestamp của SKU, cùng việc không gọi tool ghi giỏ. Trước đây grader chỉ thấy nhóm trong trạng thái mission và cộng item của mọi phương án, nên có thể chấm đạt dù phương án đang hiển thị thiếu hàng.

Kiểm thử sau các thay đổi: 161 test mission/routing/intent/grader và test API đích chạy đạt. Chưa chạy lại LLM live sau false-positive fix vì quota của toàn bộ credential fallback bị giới hạn trong run đại diện; cần chạy lại sau khi reset quota, rồi mới làm trọn current/ablation 100 × 3 lượt.

## Xác nhận sau sửa route danh mục đơn — 21/10/2026

Trong lần kiểm tra tiếp, phát hiện route đã đẩy cả yêu cầu chăm sóc da một danh mục sang planner. Đã tách rõ câu hỏi chọn một nhóm hàng để recommendation/catalog xử lý, với câu yêu cầu ưu tiên nhu cầu, chuẩn bị hoạt động hoặc lập phương án để planner xử lý. Chạy lại ma trận route: 100/100 câu đầu vào planner và 300/300 lượt không bị gắn nhầm product reference. Thêm kiểm thử hồi quy cho tư vấn da khô theo danh mục, skincare theo thứ tự ưu tiên và nhu cầu bữa sáng.

Xác nhận cuối: toàn bộ backend **878 passed**; frontend **62 passed**; production build thành công; Ruff và `git diff --check` đều đạt. Frontend local và backend `/health` đều trả HTTP 200. Chưa chạy lại benchmark LLM live 100 tình huống: lượt live 10 tình huống trước đó bị rate-limit và chạy trước các sửa route cuối, nên vẫn chỉ là kết quả chẩn đoán một phần.

## Sửa truy vấn danh mục và sở thích mềm — 21/10/2026

Kiểm tra taxonomy trên snapshot 3.081 sản phẩm cho thấy model có thể gửi đường dẫn nhóm đầy đủ như `Phi thực phẩm > Hoá mỹ phẩm > Hóa phẩm > Vệ sinh nhà`. Bộ giải cũ xử lý cả chuỗi như một nhãn đơn nên có thể trả nhãn ghép không tồn tại hoặc một parent quá rộng. Bộ giải hiện tách path và chọn nhãn danh mục sâu nhất có thật trong taxonomy; khi cụm truy vấn quá hẹp, planner thử tìm theo chính danh mục đã xác minh trước khi báo không có hàng. Test với nhóm `Vệ sinh nhà` và `Chăm sóc da` xác nhận có SKU từ snapshot.

Thêm sửa ngữ nghĩa cho `spicy=false`: nếu khách chỉ nói “ưu tiên món không cay”, sanitizer bỏ trường này khỏi bộ lọc cứng, cả khi model đặt nó trong constraint của nhu cầu, hard requirement hoặc exclusion. Sở thích vẫn được giữ để giải thích giới hạn dữ liệu cay/không cay; yêu cầu bắt buộc vẫn phải có bằng chứng trong catalog.

Sau hai thay đổi: backend đầy đủ **879 passed**, frontend **64 passed**, production build thành công, Ruff và `git diff --check` đạt. Benchmark LLM live đủ 100 tình huống × 3 lượt và ablation vẫn chưa chạy lại sau sửa; các số mô phỏng router không thay thế đánh giá chất lượng trả lời từ model.

## Live smoke sở thích không cay và lần chạy heldout mới — 21/10/2026

Chạy lại `SMH-001` qua cả 3 lượt sau khi sanitizer xử lý thêm `non_spicy`. Qwen tạo kế hoạch ở lượt đầu, fallback `openai/gpt-oss-120b` xử lý lượt chỉnh sửa, Qwen xử lý đổi ngân sách; cả **3/3 lượt qua toàn bộ checklist**. Snack/đồ uống/trái cây đều có sản phẩm từ catalog, giữ ngân sách và số người, tổng tiền hợp lệ, mission ID ổn định, giỏ không đổi. Median API latency **2,99 giây**. Kết quả: [live_shopping_mission_soft_spice_postfix_20261021.json](../../evaluation/results/live_shopping_mission_soft_spice_postfix_20261021.json).

Sau smoke, khởi chạy bộ heldout đủ 100 tình huống (300 lượt current + cùng ablation) bằng ba worker. Groq tiếp tục trả `RateLimitError` cho Qwen, OSS 120B và OSS 20B trên các credential đã cấu hình; 40/300 lượt current được ghi trước khi dừng, gồm **30 HTTP 200, 10 HTTP 502**. Worker dừng trước khi tới baseline nên không có kết quả so sánh đầy đủ và không thể dùng phần partial để kết luận chất lượng chung. Các log đầy đủ nằm tại [worker 1](../../evaluation/results/live_shopping_mission_worker-1_postfix_20261021.json), [worker 2](../../evaluation/results/live_shopping_mission_worker-2_postfix_20261021.json), [worker 3](../../evaluation/results/live_shopping_mission_worker-3_postfix_20261021.json). Lần chạy đầy đủ cần đợi quota Groq hồi lại.

## Lần chạy tuần tự và lỗi phủ định thao tác giỏ — 21/10/2026

Sau khi quota hồi đủ cho smoke ba lượt, thử lại heldout theo **một worker**, pace 5 giây, để tránh tranh quota giữa các tiến trình. Dừng sau **9/100 tình huống, 26 lượt current** vì 4 lượt trả HTTP 502; 22 lượt còn lại trả HTTP 200, nhưng chỉ **13/26 lượt qua toàn bộ grader tự động**. Đây vẫn chỉ là partial, chưa chạy ablation và không đại diện 10 archetype: các tình huống đã ghi thuộc nhóm đón khách. Trace cho thấy `RateLimitError` trên các model/key; header ghi hạn mức 8.000 token/phút và thời gian reset token khoảng 36–59 giây. Có một số `BadRequestError` khi fallback đổi model; benchmark chỉ lưu loại lỗi nên nguyên nhân cụ thể chưa xác minh được.

Run đã phát hiện lỗi an toàn luồng: câu `...; đừng cho vào giỏ vội` bị parser `cho ... vào giỏ` hiểu nhầm thành lệnh thêm hàng, còn bắt đầu ở từ `cho` trong `cho việc mời đồng nghiệp...`. Tác vụ điều khiển giỏ trả lời trước khi planner chạy, làm mất cả mục tiêu, số người và ngân sách. Đã sửa parser để bỏ mệnh đề thêm hàng bị phủ định trước khi nhận diện cart action, nhưng giữ lệnh thêm hàng dương tính nếu người dùng nêu ở mệnh đề sau. Thêm hồi quy cho tiếng Việt, tiếng Anh, câu holdout thật và trường hợp phủ định rồi thêm món khác.

Lượt chạy đầu còn phát hiện một parser khác coi “thêm nhu cầu trái cây vào phương án” là ý định mua thật. Đã mở rộng chặn an toàn cho câu có ngữ cảnh kế hoạch/phương án/nhu cầu khi không có đích cart rõ ràng. Chạy lại chính `SMH-003` sau sửa: **3/3 lượt HTTP 200 và đều gọi planner**, giữ số người, ngân sách, nhóm hàng, SKU và provenance; không gọi tool ghi giỏ, giỏ không đổi, mission ID ổn định. Median API là **4,11 giây**. Kết quả đạt **57/60 kiểm tra tự động**; mỗi lượt fail cùng `goal_identified` vì bộ expected terms không có “đồng nghiệp”, dù mục tiêu session ghi đúng “mời 5 đồng nghiệp ghé chơi sau giờ làm”. Đây là lỗi nhãn/chấm, không phải mục tiêu bị mất. Hai smoke cụ thể: [sau sửa lần một](../../evaluation/results/live_shopping_mission_negated_cart_postfix_20261021.json) và [sau sửa lần hai](../../evaluation/results/live_shopping_mission_negated_cart_postfix2_20261021.json).

Sau cả hai sửa, **412 test** API/agent/giỏ/planner/routing liên quan chạy đạt; chạy lại toàn bộ backend sau đó đạt **888 passed**. Ruff cũng đạt. Partial JSON của benchmark 100 ca đã gắn `interrupted=true`: [live_shopping_mission_heldout_sequential_20261021.json](../../evaluation/results/live_shopping_mission_heldout_sequential_20261021.json). Đây là kiểm thử hồi quy và một smoke live, chưa thay thế benchmark đủ 100 ca.

## Trạng thái chấp nhận

Chưa hoàn tất tiêu chí cuối: vẫn thiếu live benchmark đủ 100 tình huống ở cả current và ablation cùng điều kiện, phân tích naturalness/usefulness không chỉ dựa grader, và human review. Các smoke 3 lượt đạt 3/3 chứng minh luồng hoạt động trên một trường hợp, không thay thế ba hạng mục này.

## Sửa timeout planner sau lượt benchmark — 21/10/2026

Phân tích trace 502 cho thấy planner đa nhu cầu có thể chạy lâu hơn giới hạn catalog tool chung 2 giây. Replay offline của một kế hoạch có nhiều nhóm hàng mất 6,57 giây; hết 2 giây, gateway còn chạy thêm retry, tạo việc tính trùng ở thread nền. Đã đặt timeout riêng **15 giây, một lần gọi** cho `plan_shopping_mission`; các tra cứu catalog khác vẫn giữ timeout 2 giây và một retry. Nếu planner thất bại tạm thời, `/agent/respond` nay trả **503 retryable** thay vì gộp thành 502 “Groq chat provider unavailable”. Lỗi invalid/missing tool plan vẫn được phân loại là lỗi model/tool output.

Thêm hai hồi quy: planner gọi đúng một lần khi chạm timeout; API trả 503 cho `TEMPORARY_FAILURE`. Hai test đích đạt; toàn bộ backend **903 passed**, frontend **64 passed**, build production và Ruff thành công. Benchmark heldout 100 tình huống chưa được chạy lại sau sửa này; các file v1 trước đây đã lộ trong quá trình điều chỉnh nên không được tính là unseen. Cần tạo một v2 mới sau khi đóng băng code và chạy đủ current/ablation trước khi tuyên bố đạt tiêu chí cuối.

## Holdout v2 mới và giới hạn quota live — 21/10/2026

Đã sinh và đóng băng bộ holdout mới **100 tình huống / 300 lượt**, 10 bối cảnh đời thường, câu người dùng không nêu SKU/brand. Bao phủ cập nhật ngân sách/số người, thêm và thay nhóm hàng, hàng đã có sẵn, sở thích mềm, điều kiện bắt buộc chưa được catalog xác minh, cách nói địa phương, câu thiếu dấu kiểu ASR và an toàn ghi giỏ. SHA-256 bộ dữ liệu: `a1f02f9b3a873a35fd060109166d1282426eb124e1f0821f1540465a22a3d6c8`. Bộ sinh kiểm tra chống lộ chuỗi tên mặt hàng trong catalog. Không dùng kết quả từng ca để chỉnh code.

Kiểm thử sau thay đổi grader/harness: backend **907 passed**, frontend **64 passed**, Ruff sạch, build production thành công. Build còn cảnh báo chunk `model-viewer` khoảng 1 MB.

Thử live `current` có pacing trên bộ v2, sau đó dừng khi Groq trả RateLimit liên tiếp để không tiếp tục gửi request vô ích. Lượt dừng có **34/100 ca, 102/300 lượt**; 101 HTTP 200, 1 HTTP 502; 3/102 lượt đạt toàn bộ grader. Trace có 25 lần `RateLimitError`, 4 `BadRequestError`. Các kiểm tra thất bại chủ yếu là không có plan/mục tiêu/nhóm hàng/ngân sách trong state; do thiếu ablation và bị ảnh hưởng quota/fallback, con số này chỉ mô tả partial run, **không phải tỷ lệ chất lượng tổng quát hay so sánh model**. Chế độ legacy chưa chạy. Kết quả: [live_shopping_mission_heldout_v2_20261021.json](../../evaluation/results/live_shopping_mission_heldout_v2_20261021.json).

Header thực tế cho thấy giới hạn 8.000 token/phút. Ba key cấu hình không làm tăng quota vì Groq áp hạn mức theo tổ chức; tài liệu hiện hành cũng nêu 200.000 token/ngày cho mỗi model trong các mức base được liệt kê. Xem [Groq Rate Limits](https://console.groq.com/docs/rate-limits). Cần chờ quota hồi hoặc có hạn mức cao hơn rồi chạy lại cả current và controlled ablation cùng điều kiện. Bộ v2 đã lộ một phần qua lượt current; nếu cần điều chỉnh sản phẩm theo lỗi quan sát, phải tạo holdout v3 mới trước khi đánh giá unseen.

## Sửa định tuyến theo chẩn đoán partial v2 — 21/10/2026

Đọc trace partial đầu tiên cho thấy parser phần dư coi cụm mục tiêu/số người/ngân sách như một tên sản phẩm; câu tư vấn buổi xem phim vì thế gọi `recommend_products` và không tạo mission. Đã đổi ưu tiên định tuyến dựa trên ngữ cảnh hoạt động kèm quy mô/ngân sách và cách nói “gợi ý các nhu cầu / cần những nhóm gì”; câu hỏi tên hàng cụ thể như Coca Zero vẫn theo tra cứu danh mục. Các cập nhật đang có mission nay nhận cách nói “hạ trần”, sở thích “ưu tiên”, “giữ nguyên”, và điều kiện bắt buộc chưa được catalog xác minh. Câu hỏi fact thuần về mặt hàng tiếp tục vào nhánh fact.

Chạy lại bộ phân loại và các phép trích xuất API offline trên bộ v2 đã lộ: **100/100 câu mở đầu** được route vào planner; **100/100 lượt nêu nhu cầu** giữ route; **100/100 lượt sửa trong mission** route đúng sau khi thêm xử lý budget cap và hard constraint. Đây là kiểm tra định tuyến tất định trên bộ đã dùng một phần, không phải LLM quality score hoặc holdout unseen. Thêm hồi quy cho ca mục tiêu buổi xem phim, nhiều nhu cầu chưa biết tên, ưu tiên/giữ nguyên, hard constraint không đường và câu hỏi giá Coca Zero.

Chạy lại đầy đủ backend, frontend, build và benchmark LLM sau các sửa mới là bước còn lại. Không chạy live lại trên v2 đã lộ; cần dùng holdout mới. Lượt v2 trước sửa vẫn giữ nguyên trong file partial, không sửa số liệu lịch sử.

## Sửa nhận diện ngân sách và benchmark hội thoại fact — 21/10/2026

Một smoke live của `SMV2-001` tạo đúng kế hoạch ở hai lượt đầu, nhưng câu “Hạ trần xuống 230.000đ” bị chặn như yêu cầu sửa giá/tồn kho. Root cause nằm trong `_administrative_action_requested`: nhánh chống giả mạo thao tác quản trị nhận ra động từ “hạ” và số tiền nhưng danh sách từ chỉ ngân sách thiếu “trần/mức chi/khoản chi”. Đã mở rộng ngữ cảnh ngân sách; yêu cầu giảm giá SKU có giá trị cụ thể vẫn bị chặn. Hồi quy API cho cả hai ý định đạt.

Đã sửa pace của benchmark để tôn trọng `Retry-After`/token-reset kể cả khi fallback cuối cùng làm API trả HTTP 200. Smoke thứ hai sau sửa ghi được 2 lượt HTTP 200 và cả hai qua toàn bộ grader; lần gọi tiếp theo cần chờ header reset **1.664 giây**, nên đã dừng để không giữ một smoke nhỏ chờ gần nửa giờ. File JSON được giữ là partial; lượt thứ ba sau sửa chưa được xác nhận live. Regression offline cho nhận diện ngân sách vẫn đạt.

Grader nay cho phép một lượt chỉ tra cứu fact, đồng thời kiểm tra SKU/giá trong câu trả lời và so sánh mission state trước/sau để chắc kế hoạch không mất. Thêm kiểm tra rằng SKU đã bị khách từ chối không xuất hiện trong bất kỳ option nào.

Sinh và đóng băng bộ v3 mới: **100 hội thoại × 4 lượt = 400 lượt**, 10 bối cảnh, **90 hội thoại product-name-free**, trong đó 10 ca bắt đầu thiếu dịp/mục đích để kiểm tra câu hỏi làm rõ; 10 ca hỏi giá Coca-Cola Zero chai 1,5 lít trong mission rồi tiếp tục sửa ngân sách. Bộ v3 kiểm tra đổi người/ngân sách, thêm/thay nhóm, hàng có sẵn, sở thích mềm, ràng buộc chưa xác minh, lựa chọn thay thế, an toàn giỏ, tiếng địa phương và văn bản thiếu dấu. Kiểm tra chống trùng câu với v1/v2 và chống lộ tên hàng chạy khi tạo dữ liệu. SHA-256: `1d6abb41ac967c1c821980badb81c288054f1f6bbb45664494a99950c500ece3`. Artifact: [v3 JSON](../../evaluation/cases/vietnamese_shopping_mission_heldout_v3_20261021.json), [generator](../../evaluation/generate_shopping_mission_heldout_v3.py).

Thiết kế đánh giá dựa trên ba ý chính từ nghiên cứu: so state cuối với mục tiêu hội thoại và kiểm tra độ tin cậy qua nhiều lần chạy theo τ-bench; chấm mốc giữa các lượt/state dependency như ToolSandbox; tách lỗi retrieval và generation như RAGAs. Các nguồn: [τ-bench](https://arxiv.org/abs/2406.12045), [ToolSandbox](https://arxiv.org/abs/2408.04682), [RAGAs (ACL Anthology)](https://aclanthology.org/2024.eacl-demo.16/). Grader hiện không thay cho đánh giá người về tự nhiên và hữu ích.

Lần xác minh trước khi bổ sung ước lượng số lượng: **918 backend tests passed**; `ruff check backend evaluation tests` đạt; `git diff --check` đạt. Frontend không đổi trong lượt đó; lần kiểm tra trước trong cùng nhánh đã đạt 64 test và production build.

### Còn thiếu để chấp nhận

V3 chưa chạy live đủ 100 ca ở cả `current` và controlled ablation; lượt live mới nhất chỉ là smoke partial do Groq reset-token header. Chưa có review của người dùng cho độ tự nhiên/hữu ích. Vì vậy chưa thể tuyên bố tư vấn mua sắm theo nhu cầu đã đạt tiêu chí cuối; số liệu v2 partial và smoke 2/3 không được dùng làm tỷ lệ tổng quát.

## Tính số lượng theo nhóm và giữ liên quan SKU — 21/10/2026

Đã thêm phép tính số lượng xác định cho đồ uống và snack chia sẻ khi mission có số người: khoảng **250 ml đồ uống/người** hoặc **30 g snack/người**, làm tròn lên theo dung tích/khối lượng ghi trên tên SKU. Số lượng được tính lại khi đổi số người, đi vào tổng tiền và được hiển thị là ước tính; số lượng khách chốt rõ được giữ nguyên. Mỹ phẩm, giấy và hàng không tiêu dùng chung không bị nhân theo số người. Nếu catalog không ghi quy cách gói thì hệ thống giữ lượng ban đầu và yêu cầu khách xác nhận thay vì đoán.

Sửa thêm hai lỗi điều phối liên quan. Cụm như “đủ cho 5 người” được nhận diện là số người của mission thay vì điều kiện SKU không xác minh được; bộ lọc vẫn giữ lại yêu cầu độc lập đi cùng như “không cay”. Khi truy vấn mặt hàng cụ thể không có kết quả, fallback sang danh mục chỉ dùng SKU còn liên quan đến từ khóa truy vấn; nếu không khớp, báo thiếu hàng thay vì lấy một sản phẩm bất kỳ trong danh mục. Planner cũng được hướng dẫn chỉ thêm nhu cầu tùy chọn có ích rõ ràng, không thêm nguyên liệu/gia vị/dụng cụ để lấp danh sách. Grader chấp nhận “xem trận bóng” cho mục tiêu “xem bóng” nhưng không chấm đạt câu bị phủ định.

Regression bao phủ số lượng gói theo dữ liệu thật trong `data/winmart_products.json`, tổng tiền, thay đổi quy mô nhóm, số lượng khách yêu cầu, hàng chăm sóc da, quy cách SKU bị thiếu, metadata nhóm, fallback sai danh mục và cụm mục tiêu phủ định. Sau các thay đổi này, **929 backend test**, trong đó **90 test liên quan**, và Ruff toàn backend/evaluation/tests đều đạt.

Smoke live sau phần tính số lượng và nhận diện metadata: lượt đầu trả HTTP 200, qua mọi kiểm tra tự động; ví dụ năm người nhận **4 gói snack 42 g** và **1 chai nước 5 L**, với ước tính được nêu rõ trong câu trả lời. Cùng lượt đó vẫn thêm “bột canh” như một gợi ý tùy chọn không hợp ngữ cảnh; đây là bằng chứng rằng grader tự động chưa đủ đo độ hữu ích tự nhiên. Đã siết prompt và lọc fallback sau lượt này, nhưng chưa thể live recheck các sửa mới: header Groq yêu cầu chờ **518 giây** trước request kế tiếp, nên benchmark dừng sau một lượt. Kết quả ở `evaluation/results/live_shopping_mission_v3_quantity_retest_SMV3_001_20261021_r2.json` là partial, không phải tỷ lệ chất lượng.

## Hồi quy cách nói ngân sách và nhóm snack — 21/10/2026

Sửa bộ nhận diện thao tác quản trị để cụm “khoản chuẩn bị / khoản dự trù” được hiểu là phạm vi ngân sách khi khách đang chỉnh mission, thay vì bị từ chối như sửa giá hoặc tồn kho. Thêm hội thoại API ba lượt xác nhận cập nhật ngân sách 180.000đ thực sự quay lại planner và lưu ngân sách mới. Bổ sung “món lai rai”, “món nhâm nhi” và “món ăn chơi” vào cách nhận diện nhu cầu snack, kèm câu thử tiếng Việt tự nhiên.

Sau sửa, toàn bộ backend **941 passed**, kiosk **64 passed**, production build thành công, Ruff và `git diff --check` đạt. Demo trả HTTP 200 ở `http://127.0.0.1:5173/`; backend đang được frontend dùng (`127.0.0.1:8002`) báo `ok`.

Benchmark live đủ **100 hội thoại × 4 lượt** cùng controlled ablation vẫn chưa hoàn tất do giới hạn quota Groq đã ghi ở trên. Ca live một phần `SMV4-001` đã lộ trong khi chẩn đoán và không được xem là unseen; số test offline không thay thế đánh giá chất lượng phản hồi LLM hoặc đánh giá của người mua.

## Hồi quy serving-context và khăn giấy — 21/10/2026, 22:14 +07

Sửa hai lỗi lộ ở hội thoại `SMV4-001`: cụm khẩu ngữ “dễ chia sẻ/đủ cho khoảng 5 người” bị nhầm thành thuộc tính SKU chưa xác minh và chặn toàn bộ lựa chọn; “khăn lau miệng” được tìm nhầm vào khăn vải. Planner hiện bỏ metadata số người/khẩu phần khỏi hard constraint nhưng giữ yêu cầu độc lập, phân loại “khăn ăn/khăn lau miệng” bằng taxonomy hiện có của catalog, và chỉ lọc theo tên truy vấn khi truy vấn thực sự cụ thể.

Replay các đối số tool đã được ghi lại cho đủ **4 lượt** trên cùng snapshot WinMart cho thấy kế hoạch có SKU cho nhóm snack, đồ uống và khăn giấy; số người được giữ; ngân sách đổi từ **280.000đ xuống 230.000đ** ở lượt 3 vẫn hợp lệ; giỏ không bị sửa. Đây là replay offline của planner/catalog với đối số model đã lưu, không phải lần gọi LLM live sau sửa. Test đích **29 passed**; toàn bộ backend **943 passed**; frontend **64 passed**; build production và Ruff đạt.

Benchmark LLM live đủ **100 hội thoại × 4 lượt** so với controlled ablation vẫn chưa chạy xong do Groq quota; ca `SMV4-001` đã lộ và không tính là unseen. Vì vậy chưa kết luận chất lượng phản hồi live tổng quát.
