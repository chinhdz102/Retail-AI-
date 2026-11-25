# Kiểm tra tư vấn bằng text và sửa luồng — 03/11/2026

## Kết luận

**Chưa đạt mức nhân viên bán hàng thực sự.** Đã tự gọi API thật bằng text, đọc toàn bộ
câu trả lời, giữ các lượt lỗi trong mẫu số. Các sửa lỗi dưới đây có kiểm thử và có
đưa lên demo, nhưng không dùng số test qua để chứng minh chất lượng tư vấn.

Trong ảnh “Cho tao biết hôm nay là thứ mấy”, bản chép lời đúng. Gợi ý Táo Breeze là
lỗi định tuyến/phân tích tiếng Việt của ứng dụng; thay model STT không sửa được lỗi đó.

## Những phép thử thật

| Đợt | Phạm vi | Kết quả và giới hạn |
|---|---|---|
| Baseline | 16 lượt HTTP thật, 10 phiên | 12 fallback; 3 lời tư vấn yếu; 1 câu hỏi xác định món hợp lý. Có phở gói bị nói mở ra dùng được. |
| Sau vòng sửa đầu | 13 lượt, gồm 11 mua sắm + 2 câu lịch | Chấm độc lập: 0 hữu ích đầy đủ, 4 một phần, 7 thất bại; 2 control đúng. Sáu fallback nằm trong 7 thất bại. |
| Kiểm tra giao bản | Cùng 13 lượt; giãn 45 giây giữa các lượt gọi mô hình | 11 lượt mua sắm: 1 hữu ích, 4 một phần, 6 thất bại (gồm cả 4 fallback). 2 câu lịch đúng. Nguồn cố định trong phép chạy. |

Đầu ra gốc: [baseline](artifacts/live_advice_http_20261103.json),
[13 lượt sau sửa đầu](artifacts/live_advice_http_after_20261103.json),
[chấm độc lập](artifacts/live_advice_http_after_manual_20261103.json),
[giao bản](artifacts/live_advice_http_delivery_20261103.json),
[chấm giao bản](artifacts/live_advice_http_delivery_manual_20261103.json).
Runner có thể chạy lại: `evaluation/replay_live_text_advice.py`; bắt buộc chọn tệp
đầu ra mới để giữ bằng chứng cũ. Đây là câu phát triển đã lộ, không phải held-out.

Đợt giao bản có 12 lần xuất hiện thẻ hàng; tên/giá đều khớp catalog. Tuy nhiên,
tư vấn da mụn có câu suy từ nhãn non-comedogenic thành bảo đảm phù hợp; lượt thú
cưng chỉ hỏi mùi thay vì đưa hướng chọn an toàn. Hai câu này vẫn bị chấm thất bại.
Một lượt đồ thiết yếu đã chọn hợp lý 6 SKU và tổng đúng 274.400đ. Độ trễ 11 lượt
mua sắm: p50 **11,945 giây**, p95 nearest-rank **32,021 giây**; còn quá chậm cho
trao đổi tự nhiên. So với đợt trước chỉ là quan sát trên bộ nhỏ, model/generation
thay đổi; không kết luận chênh lệch có ý nghĩa thống kê hoặc do riêng một sửa đổi.

## Các nguyên nhân đã tái hiện và sửa

1. **Nhầm đại từ “tao” thành “táo” và kéo câu hỏi thông thường vào hàng hóa.**
   Tách ý định ngày/thứ trước truy xuất và trước áp ngữ cảnh món cũ; tính ngày theo
   giờ Việt Nam tại thời điểm chạy. Giữ tìm táo thật và câu mua hàng pha thông tin ngày.
2. **Mất nhu cầu ở “tư vấn chọn giúp t mấy món cần nhất đi”.** Bộ nhận diện lượt tiếp
   trước đây không nhận chuỗi động từ này. Nay giữ phạm vi/ràng buộc đã chấp nhận và
   bỏ một lần lập kế hoạch khi thích hợp. Tên hàng, ngân sách, loại trừ hay yêu cầu
   xếp hạng mới vẫn phải được hiểu lại.
3. **Chặn nhầm hướng dẫn vì có “chỉ cần”.** Bỏ chặn cả cụm vô điều kiện; kiểm tra
   lời hứa dùng ngay/lắp ráp và thời lượng thay vì cấm một cách nói. Giữ hướng dẫn
   chuẩn bị thông thường, tiêu chí mua, cảnh báo phủ định. Không coi kiểm tra regex
   hữu hạn là bộ hiểu toàn bộ công dụng sản phẩm.
4. **Câu hỏi có 0.2 kg bị coi là nhiều câu.** Chỉ chặn dấu kết câu và nhiều dấu hỏi;
   số thập phân không làm câu hỏi thất bại. Giá sản phẩm tự gõ vẫn bị kiểm tra riêng;
   không mở lỏng giá để chữa lỗi này.
5. **Mô hình tự thêm tên vào bộ lọc định danh.** Ví dụ khách chỉ hỏi thịt nhúng, planner
   lại tìm literal “thịt bò sườn”. Bộ lọc định danh nay phải bám từ khách đã nói hoặc
   truy vấn cũ được chấp nhận; phần suy luận chức năng vẫn dùng tìm nhóm hàng. Giữ dấu
   tiếng Việt để không đánh đồng “cận” và “cần”.
6. **Cân nặng người nhận thành trọng lượng gói.** Phân biệt tuổi/cân nặng trẻ, người,
   thú cưng, lượng ăn mỗi ngày với chai/gói yêu cầu. Có kiểm thử catalog bỉm/sữa thật
   với planner cố tình trả sai required_size; không gắn logic vào một SKU.
7. **Reviewer bị gợi ý lại bởi kết luận planner.** Bỏ planner_guidance khỏi ngữ cảnh
   duyệt độc lập; vẫn giữ câu khách, nhu cầu, nháp, ràng buộc, bằng chứng và vai trò.
8. **Gọi lại quá sớm sau giới hạn dịch vụ.** Đọc Retry-After, HTTP-date, reset dạng
   thời lượng cho bucket hết; cooldown theo model/credential. Không khẳng định hết
   quota ngày từ những log chỉ chứng minh áp lực token ngắn hạn.
9. **Ngữ cảnh thừa và JSON lỗi trường tùy chọn.** Bỏ placeholder có thể suy ra chính
   xác từ candidate index (offline giảm 5,26% byte thông điệp; chưa đo token mới).
   Cho phép cứu JSON bị provider trả 400 chỉ qua validator cục bộ nghiêm ngặt: giá
   trị đã có phải giữ nguyên, chỉ bổ sung default được khai báo. Không dùng failed
   generation chưa kiểm tra hoặc bỏ qua bước duyệt nội dung.

## Vì sao thêm một lần LLM duyệt chưa đủ

Trace ăn sáng có năm bước thành công, tổng **20.207 token** (16.980 input), chưa tính
lần gọi lỗi. Reviewer từng duyệt bánh bao như món mang đi ăn trực tiếp dù thiếu bằng
chứng. Sau đó lại giữ “chỉ cần” khiến renderer bác lời sửa. Trace lẩu tiếp theo cho
thấy mô hình bịa thịt đã thái mỏng; reviewer bác nhưng bản sửa còn thêm mềm/đậm vị.
Tất cả được giữ ở [trace sáng](artifacts/live_advice_direct_trace_20261103.json) và
[trace lẩu](artifacts/live_hotpot_direct_trace_v2_20261103.json).

Log xác nhận có cả lỗi ngữ nghĩa, validation và provider. Không đổ toàn bộ cho
catalog hoặc quota. Strict JSON không chứng minh đúng chức năng; reviewer accept
không chứng minh sự thật; HTTP 200 không chứng minh đã tư vấn được.

## Nghiên cứu đã đối chiếu

[Bổ sung nghiên cứu](retail_consultation_research_addendum_20261103.md) đối chiếu
RealWorldShop (EMNLP 2026) và giới hạn tự sửa của ICLR 2024; nối với ProductAgent,
PEPPER, MG-ShopDial đã đọc trước. Áp dụng: đánh giá tiến triển cả phiên, giữ phiên bản
ràng buộc, kiểm tra độc lập bằng chứng, tính cả lượt không phục vụ được. Chưa có
bản phát hành RealWorldShop được tải/tái lập, chưa làm SFT/RL hoặc benchmark mù.

## Giọng nói

STT hiện dùng **Whisper large-v3**, fallback large-v3-turbo. Thêm chẩn đoán opt-in ở
`http://127.0.0.1:5173/?voiceDebug=1`: bật lưu lượt gần nhất để nghe lại đúng audio
đã gửi, xem ngôn ngữ/capture settings, bản chép gốc và phần sửa catalog. Chỉ giữ một
lượt trong RAM; tự xóa khi tắt/xóa/reset; tải file khi chủ động chọn.

[Nghiên cứu ASR mới](vietnamese_asr_research_addendum_20261103.md) so sánh hướng đánh
giá PhoWhisper/PhoASR/Parakeet và dữ liệu giọng vùng miền. Chưa đo các model này trên
máy nên chưa đổi ASR. Kết quả noise ablation trước không chứng minh bộ khử ồn mạnh
hơn làm chép lời Việt tốt hơn. Micro, quạt/điều hòa, echo phòng thực tế vẫn cần audio
thật để kết luận. LiveKit chưa tích hợp; runtime là HTTP STT và stream TTS.

## Kiểm chứng kỹ thuật và việc còn thiếu

Backend đầy đủ ở vòng giao bản: **2.280 passed, 1 skipped**, 1 warning Starlette/httpx.
Nguồn consultation/Groq giữ nguyên trong phép chạy. API có chỉnh định dạng decorator
trong lúc test; kiểm riêng sau đó 11 test endpoint audio và 30 test định tuyến đều qua.
Frontend **120/120** và build qua; có cảnh báo chunk model-viewer lớn.

Sau đợt 13 lượt còn hoàn thiện validator cứu JSON: cho phép đúng `null → ""` mà
wire-schema đã khai báo, đồng thời yêu cầu trường bị thiếu bằng default thật;
không cho before-validator tự thay nội dung hoặc chèn giá trị khác default.
**419 test liên quan qua** sau sửa cuối, gồm fixture đúng JSON 120B đã quan sát,
test chống ép kiểu, trường dư, sai enum/quá dài và before-validator sửa tùy ý.
Đây là kiểm chứng kỹ thuật bổ sung; không gộp thành một đợt live 13 lượt mới.

Kiểm tra trực tiếp cuối bằng đúng câu lẩu xác nhận cứu JSON đã hoạt động: planner
120B thiếu `required_size` được kiểm cục bộ và nhận lại, usage để rỗng vì provider
không báo số token của failed generation. Tuy vậy, **lượt này vẫn thất bại sau
15,095 giây**: selector coi bò xay như thịt thái lát và giò sống như miếng có thể
thái, tự gõ giá/chi tiết chuẩn bị; reviewer bác nhưng hai vòng chưa hội tụ thành
câu trả lời hợp lệ. Đây là lỗi chọn dạng hàng và sửa câu, không được tính thành
“đã hết lỗi provider nên tư vấn tốt”. [Trace cuối](artifacts/live_hotpot_direct_trace_final_20261103.json).

Các test này xác nhận biên kỹ thuật; chất lượng thật phải đọc từng lượt trong artifact.
Những khoảng trống còn thấy: lựa chọn ưu tiên quá chung; trả tiêu chí cho khách thay
vì giúp quyết định; chọn SKU có công dụng chưa đủ bằng chứng; hỏi thêm không giải
quyết được thiếu bằng chứng; độ trễ nhiều giây; các vòng sửa làm tăng token và lỗi.
Muốn gọi là đạt cần tập hội thoại độc lập nhiều ngành hàng, chấm mù bởi người Việt,
và so cùng transcript/snapshot với toàn bộ lỗi dịch vụ trong mẫu số.

## Kế hoạch tiếp theo theo lỗi đo được

1. Tạo nhãn dạng/chức năng từ chính catalog: hàng chính, nguyên liệu, nhân/bột,
   phụ kiện, chế biến sẵn; dùng cặp query–product có phản ví dụ để đo đúng chức năng,
   không sửa bằng danh sách SKU hoặc đáp án lẩu riêng. Ưu tiên lỗi nghiêm trọng
   bò xay/thịt lát, giò sống/nguyên miếng và nhân bánh/thịt nguyên liệu.
2. Giảm vòng sửa toàn bộ lựa chọn: tách lỗi định danh/giá/câu hỏi có thể sửa cục bộ
   khỏi sai chức năng phải truy xuất lại. Đo cùng ngân sách token, số bước và độ trễ;
   không chỉ tăng số reviewer hoặc giảm trần token gây cụt JSON.
3. Chấm độc lập reviewer với draft sai đã giữ: đo bỏ lọt, sửa đúng thành sai,
   vòng hỏi thừa và tỷ lệ trả được lời hữu ích. Với y tế/da/thú cưng, trả hướng
   chọn có nguồn thay vì suy từ tên sản phẩm thành phù hợp hoặc bảo đảm an toàn.
4. Chỉ cân nhắc reranker/SFT sau khi có nhãn và tập kiểm riêng; chưa có bằng chứng
   chỉ đổi prompt, model theo leaderboard hay tăng lọc từ khóa là đủ.
