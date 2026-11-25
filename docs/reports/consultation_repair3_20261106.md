# Sửa tư vấn và luồng tiếng Việt — vòng 3, ngày 06/11/2026

**Đánh giá: CHƯA ĐẠT mức nhân viên thật.** Đã sửa các lỗi phần mềm tái hiện được và
kiểm tra bằng dữ liệu, provider và giao diện thật. Câu tư vấn vẫn có lúc suy diễn,
reviewer bỏ lọt lỗi và dịch vụ LLM còn trả lỗi cấu trúc/giới hạn. Không dùng số test
phần mềm để kết luận chất lượng tư vấn.

Báo cáo này tiếp nối [vòng 2](interactive_employee_round2_20261106.md). Toàn bộ câu
thử là dữ liệu phát triển đã lộ, do người dùng hoặc Codex đặt; chưa có đánh giá mù
hay nhân viên cửa hàng chấm độc lập. Không có câu trả lời gắn sẵn cho một SKU hoặc
luật “gặp lẩu thì chọn sản phẩm X”.

## 1. Lỗi tìm được và thay đổi

| Lỗi | Sửa ở vòng này | Giới hạn còn lại |
|---|---|---|
| Mất vai trò món hàng khi truyền từ planner sang selector | Giữ chức năng khách cần trong `searches.role` xuyên bước chọn/tìm lại | Role chỉ là giả thuyết mục tiêu, không là chứng cứ công dụng SKU |
| Một chai bị lẫn với gói/lốc nhiều chai | Tách `sale_unit_evidence` từ tên/uom, ưu tiên quy cách ngoài; ràng buộc theo từng vai trò, giữ qua follow-up | Không có dữ kiện thì giữ UNKNOWN; không suy đơn vị từ dung tích |
| Hiểu “nhà còn một cuộn”, “mỗi ngày một chai” thành số lượng cần mua | Phân biệt nhắc đồ đã có, mức dùng, phủ định và yêu cầu mua | Là kiểm tra ngôn ngữ có giới hạn, chưa bao phủ mọi cách nói |
| Từ bị loại hoặc từ đệm làm lệch khám phá ngành hàng | Bỏ từ thuộc điều kiện phủ định khỏi tín hiệu dương; giữ dấu tiếng Việt, xử lý “mang đi/mang theo” | Taxonomy đầy đủ vẫn có trong ngữ cảnh; không coi nhóm chưa ưu tiên là không bán |
| Dùng nháp bị bác làm mốc “nhỏ hơn/rẻ hơn” | Khi chưa có món thực sự hiện và khách không nêu mốc, hỏi mốc so sánh một lần, không tự chọn lại | Chưa giải quyết toàn bộ lỗi bộ nhớ hội thoại |
| Giá đúng bị chặn vì model gõ lại chính giá của món đã bind | Gắn lại đúng số giá trong block của chính SKU; hỗ trợ dấu phân cách và Unicode space, tránh lặp giá | Không sửa giá sai; giá thị trường, giảm giá, tổng giỏ vẫn không được hợp thức hóa |
| “Giá 28.800đ, dưới mức 50 nghìn” bị chặn nhầm | Cho phép từ đệm hẹp ở phép so trần tiền; vẫn tính lại giá/số lượng, phân biệt dưới với không quá | Không cho phép trần tiền do model tự đặt hoặc suy giá thị trường |
| Reviewer cho qua “vừa balo”, “không lo đầy bụng”, “không ga” thiếu bằng chứng | Thêm kiểm tra độc lập cho một số lời bảo đảm kích thước, cơ thể và không chứa thành phần | Đây là ranh giới literal, không phải mô hình suy diễn ngữ nghĩa tổng quát |
| Món này mượn nhãn không thành phần của món khác | Kiểm từng block với nguồn của chính sản phẩm đó | Nhãn/tên không nhắc một chất không chứng minh chất ấy vắng mặt |
| Suy giá/khối lượng sang “dùng được lâu” | Chặn một số lời hứa thời lượng, kể cả không ghi số ngày; cho phép so giá/quy cách, câu hỏi và hướng chọn | Sửa cuối, sau các capture live; mới xác minh offline và regression |
| Một lần gọi chat treo chiếm hết lượt tư vấn | Giới hạn mỗi attempt chat 12 giây trong deadline tư vấn 32 giây | Chưa chứng minh giảm latency hay tăng số lượt tư vấn đạt |
| File WAV lớn gửi STT chậm | Chuẩn bị FLAC 16kHz mono PCM16 có giới hạn, chỉ dùng nếu nhỏ hơn rõ rệt | Giữ audio gốc cho VAD/verifier; không cắt, lọc ồn, tăng gain hoặc sửa lời |
| Thử cắt STT ở 10 giây làm giọng người thất bại nhiều hơn | Trả mặc định attempt về 20 giây theo tổng ngân sách 20 giây | Không công bố timeout ngắn là cải thiện nhận diện |

Các file chính: `customer_consultation.py`, `product_packaging.py`,
`advice_claims.py`, `consultation_text.py`, `consultation_verification.py`,
`consultation_policy.py`, `groq.py`, `speech_upload.py`, `config.py`.

Không nới kiểm giá, thành phần hoặc hiệu quả để làm tăng số câu có sản phẩm. Kiến
thức thông thường vẫn được dùng cho chức năng và lời đề xuất chuẩn bị/phối hợp;
cần phân biệt lời đề xuất khách làm với việc khẳng định hàng có sẵn thuộc tính đó.

## 2. Hội thoại thật và chấm thủ công

[Bảng chấm riêng](artifacts/repair3_manual_review_20261106.json) trỏ về từng artifact
gốc và SHA-256. Chấm bằng Codex sau khi đọc câu thực tế; không phải người chấm độc
lập. Giữ cả các lượt fallback và các thử nghiệm trung gian thất bại.

14 lượt đã ghi gồm 10 lượt engine với provider thật, 3 lượt UI và 1 lượt HTTP API
nguồn cuối. Có 6 lỗi ngữ nghĩa, 7 lỗi phục vụ và 1 câu yếu; chưa có lượt đạt rubric
nhân viên. Các lượt
liên quan được thử lại ở nhiều phiên bản, nên **không chuyển các số này thành tỷ
lệ chính xác tổng quát**, cũng không coi đây là 14 khách độc lập.

| Capture | Quan sát |
|---|---|
| [Water baseline](artifacts/repair3_baseline_trace_20261106.json), 1 lượt | Chọn I-On Life 450ml nhưng tự nhận vừa balo, không ga; reviewer chấp nhận |
| [Water sau guard đầu](artifacts/repair3_after_trace_20261106.json), 1 lượt | Đổi sang lời hứa cầm một tay thoải mái/không đầy bụng; vẫn không đạt, dẫn đến guard bổ sung |
| [Water tiếp theo](artifacts/repair3_final_water_trace_20261106.json), 2 lượt | Một fallback, rồi “nhỏ hơn” không có món đã hiện làm mốc. Harness cũ còn gửi lượt tiếp sau429 bị mất dấu khi hủy; không che lỗi quy trình này |
| [Lẩu trước sửa giá](artifacts/repair3_hotpot_trace_20261106.json), 1 lượt | Fallback; có nháp chọn bò nhưng bị chặn do lặp đúng giá, nháp sửa tiếp bị reviewer bác |
| [Lẩu sau sửa ban đầu](artifacts/repair3_final_hotpot_diagnostic_20261106.json), 1 lượt | Có lựa chọn bò, nhưng tự gọi dạng miếng nguyên chưa có căn cứ; câu còn vụng |
| [Nước rửa chén trước sửa “dưới mức”](artifacts/repair3_delivery_household_trace_20261106.json), 1 lượt | Đúng chai/giá, lý do chủ yếu lặp quy cách và “đáng tiền”; chưa đạt |
| [Nước rửa chén sau sửa](artifacts/repair3_verified_household_trace_20261106.json), 2 lượt | Lượt đầu còn suy “dùng lâu dài/không lo ngân sách”; lượt đổi sang túi fallback. Bật Python-wide exception tracing nên không dùng latency làm tốc độ sản phẩm |
| [Dầu gội diagnostic](artifacts/repair3_shampoo_diagnostic_20261106.json), 1 lượt | Chặn đúng nháp “công thức cân bằng/không khô da”; bản sửa vẫn hứa 300g dùng được thời gian dài và reviewer cho qua. Đây là căn cứ sửa duration cuối |
| [UI lẩu](artifacts/repair3_ui_20261106.json), 2 lượt | Cả hai chỉ hiện trục trặc, trước các sửa cuối về giá/transport |
| [UI dầu gội](artifacts/repair3_verified_ui_20261106.json), 1 lượt | Bản đã có sửa ngân sách/transport vẫn fallback; capture này trước guard duration cuối |
| [API dầu gội nguồn cuối](artifacts/repair3_final_api_20261106.json), 1 lượt | Sau guard duration và restart: HTTP200 nhưng chỉ trả fallback, không có sản phẩm,32,637giây; log ghi deadline, có lỗi rate limit/timeout. Không bật Python tracing; hash nguồn trước/sau khớp |

![Giao diện thực tế vẫn có lượt tư vấn thất bại](artifacts/repair3_verified_ui_20261106.jpg)

[Replay offline các lựa chọn đã capture](artifacts/repair3_captured_native_replay_20261106.json)
dùng nguồn cuối: 14 bản chọn, 7 bị chặn và 7 qua ranh giới render. Hai lời hứa
thời lượng của dầu gội/nước rửa chén bị chặn. Câu đúng về giá “dưới mức” render
được. **Render được không có nghĩa tư vấn đúng**: lời “miếng nguyên” ở ca lẩu vẫn
cần đánh giá ngữ nghĩa; không đổi nhãn các capture cũ thành live pass.

35 ví dụ gốc vẫn nằm trong `evaluation/cases/customer_consultation_35_dev_20261031.json`.
Vòng này không hoàn thành một lượt live mới cho đủ cả 35 dưới nguồn cuối. Regression
giả lập nhiều ngành hàng kiểm hợp đồng phần mềm, không thay cho lượt hội thoại đó.

## 3. STT, VAD và TTS

Model chính vẫn là **`whisper-large-v3`**, dự phòng `whisper-large-v3-turbo`, ép
ngôn ngữ `vi`. Demo có Zipformer Việt local làm verifier phụ khi bất định; không
đổi transcript thành đáp án của verifier và không coi hai model đồng ý là chân lý.

| Lần chạy14 file | HTTP thành công | Giọng người | Kết luận |
|---|---:|---|---|
| [Trước timeout10s](artifacts/repair3_vietnamese_audio_20261106.json) | 12/14 | FLEURS/Nghệ An504; ViMD200 | Chưa ổn định |
| [Thử timeout10s](artifacts/repair3_vietnamese_audio_delivery_20261106.json) | 11/14 | Cả ba502 | Thử nghiệm làm availability tệ hơn; đã bỏ mặc định10s |
| [FLAC có giới hạn + attempt20s](artifacts/repair3_vietnamese_audio_transport_20261106.json) | 14/14 | Cả ba200 | Kết quả quan sát tốt hơn ở lượt này; nhiều biến thay cùng lúc và điều kiện dịch vụ thay đổi, chưa tách quan hệ nhân quả |

Chi tiết lượt cuối:

- FLEURS11,34giây: HTTP200 trong7,276giây; WER0 theo chuẩn hóa hiện có.
- ViVoice Nghệ An47,17giây: HTTP200 trong17,367giây; WER0,3333, còn sai nhiều từ,
  `needs_confirmation=true`.
- ViMD3,65giây: HTTP200 trong14,934giây; WER0,3333, “rong riềng” vẫn thành “dòng
  diệt”; yêu cầu xác nhận.
- Sáu clip retail từ Azure TTS: đều200; câu lẩu/phủ định thịt heo và nhờ tư vấn
  đúng. Số1lít/100ml/150nghìn đúng nghĩa nhưng khác cách viết làm WER tăng.
  **Ariel→A-RIN, mùi nồng→mùi nông vẫn sai**, có cờ xác nhận.
- Năm ESC-50 không lời: chó, gõ cửa, bàn phím, máy hút bụi, đồng hồ báo thức đều
  trả text rỗng, `speech_detected=false`, bị chặn trước Whisper.
- TTS Azure `vi-VN-HoaiMyNeural`: HTTP200, MP3 30.528byte,2,360giây đáp ứng.
  Chưa có chấm nghe tự nhiên/độ rõ độc lập;200 không chứng minh chất lượng nghe.

[Kiểm waveform/size offline cuối](artifacts/repair3_upload_file_check_v2_20261106.json):
FLEURS725.818→187.519byte; Nghệ An1.509.598→848.802byte; chuẩn bị khoảng0,03–0,07giây,
giữ số mẫu16kHz và sai số PCM tối đa khoảng3,1e-5. ViMD giữ WAV gốc vì downmix có
đỉnh vượt khoảng PCM16; không cắt đỉnh hay đổi gain để ép nén. Thử đầu phát hiện
sai số đỉnh0,0556 ở file này và đã sửa nhánh giữ bản gốc. FLAC lossless so với
PCM16 đã chuẩn hóa; resampling/quantization không được mô tả là bit-identical
với file gốc. MP3/Opus nhỏ không được mã hóa lại; encoder không khả dụng thì dùng
upload gốc sau khi đã qua acoustic check.

**Chưa kiểm micro vật lý, quạt/điều hòa tại phòng người dùng, browserVAD trên
capture mic thật, loa/echo, ngắt lời hoặc LiveKit end-to-end.** Không nâng kết quả
replay file thành chứng nhận những luồng đó đã tốt. Không thêm denoiser mới chỉ
vì tên model mạnh hơn; [ablation lọc ồn trước](noise_filtering_20261103.md) đã ghi
các trường hợp làm WER tệ hơn.

## 4. Tài liệu nghiên cứu và mức áp dụng

| Nguồn chính đã đối chiếu | Đọc/áp dụng | Không tuyên bố |
|---|---|---|
| [When Can LLMs Actually Correct Their Own Mistakes?, TACL2024](https://aclanthology.org/2024.tacl-1.78.pdf) | Đọc abstract, phần mở đầu và phân loại feedback; dùng kiểm dữ kiện ngoài model thay vì tin phiếu tự duyệt | Chưa tái lập benchmark, không suy tự-review luôn cải thiện |
| [Key Condition Verification / ProCo, EMNLP2024](https://aclanthology.org/2024.emnlp-main.714.pdf) | Đọc cơ chế che điều kiện rồi tái dựng để kiểm câu; định hướng đối chiếu điều kiện khách cần với bằng chứng | Không triển khai thuật toán ProCo hoặc dùng điểm paper làm điểm dự án |
| [Personalization Matters, Findings AACL2026](https://aclanthology.org/2026.findings-aacl.199/) | Chỉ đọc abstract/metadata: bộ nhớ sở thích và đánh giá cả trajectory; giữ điều kiện/món thực sự đã hiện | PDF chưa đọc được; nghiên cứu người dùng n=5 mang tính thăm dò, không xác nhận kiosk Việt |
| [Groq Speech-to-Text](https://console.groq.com/docs/speech-to-text) | Đọc phần preprocess, ngôn ngữ và metadata;16kHz mono/FLAC làm căn cứ thử transport | Khuyến nghị FLAC không chứng minh giảm WER hay giải quyết giọng vùng miền |
| [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs) | Xác nhận các model cấu hình được liệt kê hỗ trợ strict; vẫn kiểm schema và hợp đồng mua sắm tại app | Schema đúng không chứng minh phù hợp/có ích; capture400 không được giải thích là model không hỗ trợ |
| [Qwen3.8-27B trên Groq](https://console.groq.com/docs/model/qwen/qwen3.8-27b) | Đọc chế độ suy luận/instruct và hướng dẫn dùng | Chưa có so sánh cùng context đủ mạnh để đổi mọi stage/model hoặc gọi kỹ thuật nào mạnh nhất |

Đóng góp của vòng này là sửa lỗi đã quan sát và thử nghiệm có giới hạn, không phải
huấn luyện model mới. Các paper về ChatCRS, clarification, ProductAgent, shopping
reasoning và Vietnamese ASR đã có ranh giới đọc/áp dụng ở báo cáo vòng2; không chép
điểm của tác giả thành điểm demo.

## 5. Lỗi dịch vụ và quy trình đánh giá

Capture có `json_validate_failed`400 ở Qwen strict: có lần JSON bị cắt, có lần trả
`answer` thay hợp đồng `picks/text`. Không khôi phục JSON bị cắt thành hợp lệ. Chỉ
giữ cơ chế phục hồi default đã định nghĩa và kiểm cả cấu trúc/giá/ý nghĩa tiếp.

Nhiều lỗi429 có giới hạn header 8.000TPM. Diagnostic dầu gội có `Retry-After:627` giây
ở một slot; không tự suy là hết quota ngày từ con số này. Đã chờ qua mốc bảo thủ
trước khi restart nguồn cuối và gửi đúng một lượt API; lượt đó vẫn fallback.
Không gửi thêm lượt sau lần API cuối. [Mốc chờ bảo thủ](artifacts/repair3_provider_cooldowns_20261106.json)
lưu riêng từ trace; không chứa key. Thêm key không sửa được lỗi suy luận/reviewer.

Harness được sửa để giữ dấu429 kể cả khi lượt sau bị deadline hủy; dừng gửi lượt
khách tiếp theo khi thấy429. Bổ sung xuất cooldown ra artifact để lần sau có thể
nạp lại; trước sửa, nhiều file trace không có trường này nên
`--respect-cooldowns` không nạp được mốc nào. Giữ rõ hạn chế đó khi đọc capture cũ.

Python-wide tracing đã làm méo thời gian, đặc biệt follow-up đổi túi38,87giây.
Một profile offline trước bước gọi LLM không bật tracing mất khoảng0,75giây dưới
cProfile; không đủ kết luận toàn luồng nhanh. Tracer cuối chỉ theo file ứng dụng,
nhưng không có phép đo latency live sau sửa tracer.

## 6. Kiểm tra phần mềm và nguồn

- Full backend trước guard thời lượng cuối: **2.791 passed,1 skipped,1 warning**,
  310,36giây. [JUnit](artifacts/backend_repair3_verified_20261106.xml).
- Sau guard cuối: **921 passed,1.893 deselected,1 warning**,33,56giây, tập
  consultation/customer wording/shopping guidance. [JUnit](artifacts/repair3_duration_final_related_20261106.xml).
- Full backend nguồn cuối: **2.813 passed,1 skipped,1 warning**,289,29giây.
  [JUnit](artifacts/backend_repair3_final_source_20261106.xml). Các hash nguồn vẫn
  khớp manifest sau lần chạy.
- Ruff scoped cho các file sửa qua. Kiểm cooldown artifact offline qua.
- Skip là quyền tạo symlink Windows; warning Starlette/httpx. Không sửa frontend
  ở vòng này, không ghi test/build frontend lịch sử thành lần chạy mới.
- Các XML/capture thử nghiệm thất bại vẫn được giữ. Không cộng test scoped vào
  full-suite để công bố một tổng pass mới.

[Manifest cuối](artifacts/repair3_delivery_manifest_20261106.json) ghi hash nguồn,
artifact, kết quả kiểm thử và ranh giới runtime/capture. Audio replay cuối có hash
trước/sau ổn định. Guard thời lượng được thêm sau replay và các trace/UI trước;
lượt API nguồn cuối riêng vẫn thất bại. Không gán ngược các capture trước đó cho
toàn bộ mã cuối.

## 7. Phần còn cần hoàn thiện

1. Xử lý sự không ổn định của JSON/provider trên cùng context cố định, đo số lượt
   tư vấn hữu ích theo token/latency. Hiện có cả400,429 và lời sai được reviewer
   chấp nhận; không thể quy mọi fallback thành lỗi DB.
2. Kiểm chất lượng lựa chọn/lý do bằng hội thoại người thật chấm và tập giữ kín
   theo nhiều ngành hàng, cả lời chuẩn bị thông thường đúng lẫn lời hứa sai. Các
   guard literal hiện tại không thay một bộ kiểm ngữ nghĩa đã được đánh giá.
3. Thêm dữ kiện nhãn có nguồn cho các thuộc tính cần thiết; đồng thời tiếp tục
   dùng LLM để hiểu công việc, đánh đổi và cách dùng thông thường. Không viết đáp
   án sẵn cho35 câu hoặc bắt khách tự biết mọi thuật ngữ để được tư vấn.
4. Tiếp tục so ASR cùng audio cho lỗi brand, phủ định, số và giọng tự nhiên; sau
   đó đo capture mic/ồn/echo/ngắt lời thật. Cờ xác nhận giảm rủi ro gửi sai ý,
   chưa làm lời chép trở nên chính xác.

Demo vẫn phục vụ kiểm thử có kiểm soát. **Chưa thể bàn giao với lời cam kết “đã
như nhân viên thật” hoặc “STT/VAD/TTS/LiveKit đều ổn”.**
