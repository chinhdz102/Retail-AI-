# Báo cáo kiểm tra tư vấn và giọng nói — 01/11/2026

**Chưa đạt mức nhân viên thật, chưa đủ điều kiện bật tư vấn thử nghiệm mặc định.**
Đã sửa được lỗi VAD ngắt TTS quá sớm, ghi âm dự phòng gửi câu bị cắt, cleanup TTS khi
hủy trước byte đầu, cleanup AudioContext và một số lỗi phân tích tiếng Việt/định tuyến.
Đã nghiên cứu và chạy cả provider thật, model VAD thật và kiểm thử giả lập.
Kết quả tư vấn vẫn yếu; thêm một lượt LLM review chưa đem lại cải thiện được chứng minh.

## Phạm vi và kế hoạch

- [Kế hoạch đã thực hiện](consultation_voice_workplan_20261101.md).
- [Paper, hướng dẫn chính thức và dữ liệu hãng đã đọc](consultation_voice_research_20261101.md).
- Không có bảng câu hỏi → đáp án/SKU trong runtime. Các ví dụ và đánh dấu thủ công ở
  evaluation là development data.
- Không có LiveKit runtime/dependency/config. Luồng thật: browser Silero v5/ONNX →
  WAV/MediaRecorder → Groq Whisper → agent → Azure/ElevenLabs ticket stream → audio.
- Không tạo đơn hàng thật. Demo opt-in vẫn chạy tại http://127.0.0.1:5173/;
  `CUSTOMER_CONSULTATION_ENABLED` mặc định false và chỉ áp dụng development.

## Thay đổi có kiểm chứng

1. `onSpeechStart` chỉ bắt đầu tính thời gian; chỉ `onSpeechRealStart` mới đổi RECORDING
   để ngắt TTS. Onset ngắn rồi misfire không còn tự ngắt lời đọc.
2. Nhánh ghi âm dự phòng cũng từ chối câu vượt 12 giây, không gửi phần câu bị cắt.
   Lỗi được hiện để khách chia yêu cầu; đây vẫn là giới hạn trải nghiệm cần xem lại.
3. TTS đóng upstream khi hủy trước byte đầu. Cleanup có AnyIO shield và giới hạn 2 s,
   được test với CancelledError, lỗi giữa stream và consumer close/cancelled scope.
4. Giới hạn phần copy upload STT ở 10 MB + 1 byte trước khi kiểm tra kích thước.
   Điều này không biến multipart parser thành bộ giới hạn upload ở reverse proxy.
5. Không để promise đóng AudioContext bị reject thành lỗi chưa xử lý; track mic vẫn dừng.
6. Chặn output Groq có finish_reason length/content_filter; giữ metadata kết thúc.
   Khi gặp 429, tạm bỏ qua đúng cặp model/credential trong thời gian retry-after có giới hạn,
   không ngủ chặn request; hết cooldown sẽ thử lại. Không khẳng định đã giải quyết quota.
7. Projection đưa bằng chứng có nguồn/phạm vi vào synthesis. Thêm vai trò mỗi nhóm trong
   nhiệm vụ, lấy ứng viên trải đều các mức giá thay vì chủ yếu vài món rẻ nhất; tối đa 48.
   Mỗi tên phải có placeholder ràng buộc; giá có khoảng trắng Unicode không được lọt kiểm tra.
8. Thêm review bản nháp và cả fallback chỉ có tên/giá; review không chứng minh mọi claim đúng.
9. Tra giá/vị trí/mua hàng đi qua controller đã có thay vì mất một lượt lập kế hoạch sinh.
   Parser phân biệt không tìm thấy với loại trừ thuộc tính, đại từ nó với English no,
   và ăn no với tham chiếu sản phẩm cũ. Test dùng nhiều nhóm khác nhau.
10. Sửa tài liệu voice đang mô tả sai 800/400/1400 ms, 30 s và mic luôn dừng khi đọc.
    Runtime hiện dùng 600/250/900 ms, threshold 0.55/0.35, giới hạn 12 s, nghe barge-in khi TTS.

## 35 câu người dùng: kết quả thực tế

Đây là lượt chạy đầy đủ ở phiên bản thử nghiệm có review, **trước** các sửa định tuyến/
parser cuối. Hash lúc bắt đầu nằm trong artifact, không đổi nhãn run này thành điểm của
phiên bản cuối. Mỗi câu bắt đầu phiên mới. Các câu này đã lộ từ trước; agent tự đọc,
không chấm mù, không có nhân viên/người dùng độc lập tham gia.

Tự rà: **16 hành động đầu hợp lý, 8 yếu,
11 lỗi**. Hợp lý ở lượt đầu thường chỉ là hỏi đúng thông tin còn thiếu,
không phải đã tư vấn xong. Có 8
lượt fallback; chỉ 1/35 có thẻ sản phẩm.
Median 2.562 s, max 13.859 s.
Qwen bị giới hạn, kết quả chủ yếu từ gpt-oss-120b/20b dự phòng. Không gộp lỗi provider
với bằng chứng mô hình không hiểu ngữ nghĩa, cũng không bỏ chúng khỏi trải nghiệm khách.

Điểm rõ nhất: case 21 vẫn suy ra vị ngọt từ không đường và gán chất xơ cho cả hai món.
Reviewer chấp thuận sai. Các câu không rõ nhóm có thể hỏi tiếp, nhưng nhu cầu đã đủ rõ
lại hay bị hỏi ngược. Không có bằng chứng đợt thử này tốt hơn đợt 17 hợp lý/9 yếu/9 lỗi trước.

| Câu | Đánh dấu của agent | Nhận xét sau khi đọc đáp án |
| --- | --- | --- |
| 1 | weak | Hỏi đồ gì quá chung; nên đưa hướng bếp/vệ sinh/cá nhân để khách dễ chọn. |
| 2 | fail | Hỏi khách có muốn được đề xuất dù chính khách vừa nhờ tư vấn; chưa tiến bước. |
| 3 | fail | Rơi vào câu trả lời dự phòng, chưa tư vấn được. |
| 4 | reasonable_first_action | Hỏi tuổi trẻ có ích; cách nói trẻ bạn còn gượng. |
| 5 | reasonable_first_action | Thiếu sở thích của mẹ; hỏi tiếp hợp lý nhưng có thể đưa hướng chọn. |
| 6 | fail | Câu trả lời dự phòng, chưa chọn được đồ ăn tiện. |
| 7 | weak | Hỏi khu vực có ích nhưng chưa đưa bộ đồ dọn tối thiểu hoặc hướng cụ thể. |
| 8 | weak | Có số người/bữa/ngân sách nhưng tiếp tục đẩy việc chọn thực đơn cho khách. |
| 9 | fail | Câu trả lời dự phòng cho nhu cầu dầu gội dưới giá. |
| 10 | reasonable_first_action | Hỏi số người và bữa cần mua trước khi tính cả tuần. |
| 11 | reasonable_first_action | Phiên mới thiếu nhóm hàng, cần hỏi nhóm/ngân sách. |
| 12 | reasonable_first_action | Phiên mới thiếu món gốc nên chưa thể tìm hàng rẻ hơn. |
| 13 | fail | Câu trả lời dự phòng cho nhu cầu giá trị chưa rõ nhóm. |
| 14 | reasonable_first_action | Chưa có hai SKU trong phiên mới, cần làm rõ. |
| 15 | reasonable_first_action | Hỏi dạng nước/bột trước khi so hai hãng; chưa bịa hiệu quả. |
| 16 | reasonable_first_action | Thiếu sản phẩm đang nhắc ở phiên mới. |
| 17 | reasonable_first_action | Thiếu cặp sản phẩm đang so ở phiên mới. |
| 18 | reasonable_first_action | Thiếu nhóm hàng, không tự bịa trải nghiệm cá nhân. |
| 19 | reasonable_first_action | Thiếu nhóm/cặp để so cùng mức giá. |
| 20 | fail | Câu trả lời dự phòng cho da nhạy cảm. |
| 21 | fail | Suy ra vị ngọt nhẹ từ không đường; gán giàu chất xơ cho cả hai SKU thiếu căn cứ. |
| 22 | weak | Hỏi nhóm vệ sinh có ích nhưng hỏi lại yêu cầu an toàn mèo khách đã nêu. |
| 23 | weak | Hỏi loại đồ uống nhưng chưa chủ động lựa chọn có ghi không/ít đường. |
| 24 | reasonable_first_action | Chỉ biết phòng nhỏ, thiếu hẳn món cần mua. |
| 25 | fail | Tự giả định là lăn/xịt khử mùi dù khách chưa nói nhóm hàng. |
| 26 | fail | Câu trả lời dự phòng cho vị trí nước giặt; đã sửa định tuyến sau run này. |
| 27 | weak | Hỏi mua cho ai chưa đủ phân biệt sữa màu xanh; cần thông tin nhận diện có ích hơn. |
| 28 | fail | Câu trả lời dự phòng cho mì Hàn lần mua trước. |
| 29 | fail | Câu trả lời dự phòng cho vị trí khăn giấy; đã sửa parser/định tuyến sau run này. |
| 30 | reasonable_first_action | Có 1 lít nhưng phiên mới chưa biết nhóm hàng. |
| 31 | weak | Chỉ xác nhận lại ý thay thế, chưa hỏi món gốc để tìm. |
| 32 | reasonable_first_action | Quảng cáo TikTok không đủ nhận diện, cần nhóm/tên. |
| 33 | reasonable_first_action | Tốt vừa tiền nhưng chưa biết loại hàng. |
| 34 | reasonable_first_action | Cả nhà chưa xác định sản phẩm, câu hỏi có hướng nhóm. |
| 35 | weak | Câu hỏi màu vàng nào còn vòng, cần hỏi nhóm/hãng/dấu hiệu nhận diện. |

Nguồn: [35 đáp án gốc](artifacts/customer_consultation_35_grounded_review_20261101.json),
[annotation thủ công](artifacts/customer_consultation_35_grounded_review_manual_20261101.json).

### Kiểm tra lại sau sửa

Sáu câu 3/9/20/21/26/29 được chạy lại sau sửa định tuyến/cooldown. 9/20/21 vẫn fallback;
3 thành hỏi thêm. 26 đi qua catalog được; 29 lộ lỗi phủ định tiếng Việt, sau đó được sửa.
Đây không phải một lần chạy lại đủ 35 câu.

Replay cuối của hai câu tra vị trí:

| Câu | Thời gian s | Controller | Thẻ hàng |
| --- | --- | --- | --- |
| 26 | 1.095 | catalog | 5 |
| 29 | 0.492 | catalog | 3 |

Case 26 trả kệ theo sơ đồ đề xuất và nói chưa xác minh vị trí thật, nhưng còn dài và
nhiều mã kệ/quy cách. Case 29 bỏ được lỗi fallback/phủ định nhưng **vẫn trả gợi ý hàng,
không trả vị trí**; case này vẫn chưa đạt. Không lấy hai HTTP 200 làm hai lượt tư vấn tốt.
Nguồn: [6 câu sau sửa](artifacts/customer_consultation_35_voice_delivery_checks_20261101.json),
[2 câu tra cứu cuối](artifacts/customer_consultation_35_lookup_delivery_20261101.json).

### Hội thoại nhiều lượt

Chạy lại sáu journeys, tổng 16 lượt ở
phiên bản sau cooldown/định tuyến, trước parser cuối. Các chuỗi vẫn cho thấy hỏi vòng,
không chốt được danh sách, hỏi lại loại bò sau khi khách đã nói thái lát. Không lấy việc
HTTP 200 hoặc nhớ được từ sữa làm bằng chứng hội thoại tốt.

| Journey | Lượt đã chạy | Lượt có thẻ hàng |
| --- | --- | --- |
| milk-comparison | 6 | 0 |
| volume-reference | 3 | 0 |
| household-discovery | 2 | 0 |
| weekly-budget | 2 | 0 |
| hotpot-meat | 2 | 0 |
| unseen-battery | 1 | 0 |

[Toàn bộ journeys](artifacts/customer_consultation_journeys_voice_delivery_20261101.json).

## STT với giọng người thật

Gọi endpoint đang chạy, Groq Whisper large-v3 thực tế; WAV công khai có tham chiếu và
giấy phép CC-BY-4.0. WER/CER dùng normalization của harness, là tỷ lệ tổng lỗi/tổng từ
hoặc ký tự tham chiếu, không phải trung bình phần trăm từng mẫu.

| Bộ diagnostic đã lộ | Mẫu được nhận | Exact theo normalization | WER | CER | Median / max ms |
| --- | --- | --- | --- | --- | --- |
| FLEURS vi_vn test | 12/12 | 4 | 7.41% | 4.43% | 886 / 19693 |
| ViVoice34, 7 tỉnh metadata | 7/7 | 0 | 21.30% | 10.92% | 3002 / 3966 |

ViVoice có mẫu Nghệ An WER khoảng 32.68%, Thừa Thiên Huế 31.17%, Quảng Trị 50.58%.
Tỉnh trong metadata không phải nhãn giọng đã được xác minh; một mẫu/tỉnh không đại diện
dân số. Các đoạn là đọc văn bản, nhiều đoạn dài hơn 12 s: gọi API trực tiếp **bỏ qua**
giới hạn controller của kiosk. Không gọi chúng là end-to-end micro hay accuracy retail.
Có một lượt FLEURS mất 19.693 s, gần deadline STT 20 s; latency tail vẫn là vấn đề.

Control: im lặng, tone 440 Hz, nhiễu trắng đều HTTP 200 nhưng speech_detected=false và
không có lời. File rỗng 422, MIME sai 415, quá 10 MB 413. File giả không giải mã được
vẫn thành **502** do provider reject; phân loại lỗi input này chưa tốt và chưa sửa.

[STT từng mẫu, transcript và tham chiếu](artifacts/voice_pipeline_stt_20261101.json).

## VAD với model thật

Chạy model **v5 .onnx** được app phục vụ, ONNX Runtime Web WASM và FrameProcessor của
package 0.0.31, frame 512 ở 16 kHz, cấu hình đúng production source. Không dùng helper
NonRealTimeVAD vì helper của phiên bản đang cài chạy legacy model.

- 19/19 file người thật
  có SPEECH_REAL_START.
- Ba control im lặng/tone/nhiễu trắng và đoạn người nói cắt ngắn không phát event speech.
- Có 13 segment dài hơn 12 s. Controller sẽ từ chối;
  đây là giới hạn chưa giải quyết, không phải 13 lần VAD test pass.
- Chưa có nhãn thời điểm bắt đầu/kết thúc chuẩn, nên không tính precision/recall hay khẳng
  định mọi pause đã đúng. Ngưỡng ms được lượng tử theo frame; không hứa thời gian tuyệt đối.
- 14 asset trên localhost khớp byte với file local (gồm model/worklet/runtime/support files).
  HTTP 200 được kiểm tra bằng bytes/hash để tránh nhầm SPA fallback.

[Replay VAD và timeline](artifacts/local_vad_audio_replay_20261101.json),
[asset delivery](artifacts/voice_asset_delivery_20261101.json).
Chưa kiểm tra capture AudioWorklet trên browser thật, AEC, loa/mic phòng, self-capture,
vừa đọc vừa nói xen và pause dài trong câu trên thiết bị kiosk.

## TTS và audio → STT → agent → TTS

Provider thật Azure; vé một lần, stream audio/mpeg, ffmpeg giải mã thành PCM được.
Thời gian sau bao gồm tạo ticket và nhận stream, không bao gồm decode/reuse check:

| Locale | Voice | Byte đầu ms | Stream xong ms | Audio s | Gọi lại vé |
| --- | --- | --- | --- | --- | --- |
| vi | vi-VN-HoaiMyNeural | 1044 | 1400 | 9.984 | 404 |
| en | en-US-JennyNeural | 243 | 450 | 5.856 | 404 |
| zh | zh-CN-XiaoxiaoNeural | 146 | 244 | 4.536 | 404 |

Chuỗi tiếng Việt có 300g, 45.000đ, 1,5 lít, 32.500đ và ký tự & < >. Test này chỉ xác
nhận request/escape/decode, **chưa có người nghe chấm đọc số, ngữ điệu hay độ tự nhiên**.
Text quá giới hạn nhận 422. Client ngắt sau chunk đầu có tái hiện trên HTTP; việc
upstream đóng được xác nhận thêm bằng test lifecycle giả lập, không suy ra chỉ từ client close.
ElevenLabs chưa được gọi thật do Azure đang được cấu hình; browser fallback/autoplay
được test bằng mock, chưa xác nhận trên thiết bị/browser người dùng trong đợt này.

Bốn replay retail **synthetic từ Azure** đều qua STT → agent HTTP 200 → TTS giải mã được.
Ba transcript exact theo normalization, một WER 20%; không lấy TTS tổng hợp làm người thật.
Các câu trả lời vẫn hỏi thừa hoặc báo chưa có thịt dưới ngân sách dù truy xuất chưa chứng
minh điều đó; không có thẻ hàng trong bốn lượt. **Transport 4/4 không đồng nghĩa tư vấn 4/4 đạt.**

[TTS](artifacts/voice_pipeline_tts_20261101.json),
[E2E synthetic có transcript và lời trả](artifacts/voice_pipeline_e2e_20261101.json).
Các run TTS ban đầu được giữ riêng; harness từng gửi text quá dài khi định test cancel
và nhận 422 đúng thiết kế, sau đó mới dùng input hợp lệ để kiểm tra cancel.

## Hồi quy kỹ thuật, demo và phạm vi xác nhận

- Full backend trong đợt: **1.638 passed, 1 skipped** (Windows không có quyền tạo symlink),
  trước các sửa cuối cooldown/parser/cleanup shield. Sau đó chạy các nhóm ảnh hưởng riêng;
  không tuyên bố con số full là lần chạy lại tất cả trên hash cuối.
- Nhóm API/lifecycle: **397 passed** ở thời điểm sửa hủy sớm; lifecycle cuối gồm 4 tình huống.
- Nhóm consultation/Groq/Azure/ticket/lifecycle sau cooldown: **114 passed**.
- Nhóm parser/consultation/Groq/lifecycle cuối: **252 passed**; số không được cộng với nhóm khác.
- API + Vietnamese intent + product-search benchmark sau parser cuối: **561 passed**.
- Frontend cuối: **101 passed / 9 files**; production build thành công. Có warning chunk
  >500 kB của model-viewer và jsdom chưa triển khai media/canvas; browser audio vẫn cần thử thật.
- Ruff cho các file thay đổi được chạy; kết quả cuối nằm trong artifact, không bỏ qua fail.
- Browser control đợt này báo `browser.user is unavailable` khi kết nối tab sẵn có.
  Không có ảnh/UI verification mới; đã dùng API và replay audio, không nhận là đã thử loa/mic.
- Demo bật experimental để người dùng xem lỗi thật. Production flag vẫn false. Không deploy,
  commit, PR hoặc thay dữ liệu đơn hàng thật.

Logs: [full backend](artifacts/consultation_voice_backend_full_20261101.txt),
[API/lifecycle](artifacts/consultation_voice_api_lifecycle_20261101.txt),
[contracts](artifacts/consultation_voice_final_contracts_20261101.txt),
[logic cuối](artifacts/consultation_voice_logic_delivery_20261101.txt),
[API sau parser cuối](artifacts/consultation_voice_api_delivery_20261101.txt),
[frontend](artifacts/consultation_voice_frontend_final_20261101.txt),
[build](artifacts/consultation_voice_build_final_20261101.txt),
[lint](artifacts/consultation_voice_lint_final_20261101.txt).

## Phần còn phải giải quyết để nghiệm thu

1. Giảm hỏi vòng và bảo đảm chọn được hàng đúng mục đích; meal/week basket cần dữ liệu
   vai trò, số người/bữa/đồ sẵn và đánh giá người dùng, không chỉ tổng tiền hợp lệ.
2. Reviewer cần đánh giá claim theo bằng chứng và người chấm độc lập; không phát hành
   dựa trên boolean LLM. Việc nối nguồn mới là hạ tầng, chưa bảo đảm đúng mọi câu.
3. Quota/provider ổn định: inventory đã được đọc, không có model chat tổng quát khác
   để đổi thử ngay trên cùng provider. Không tự mua hoặc đổi nhà cung cấp.
4. Thu tập tiếng nói retail tự nhiên có consent, tham chiếu và nhãn turn boundary;
   thử thật micro/loa, tiếng ồn, echo, barge-in/backchannel, autoplay và thiết bị lỗi.
5. Xem lại giới hạn 12 s, timeout tail, lỗi file hỏng bị báo 502; tránh gửi câu bị cắt
   nhưng vẫn cần trải nghiệm phục hồi tốt hơn.
6. Chỉ đánh giá migration LiveKit sau khi có nhu cầu đo được. Hiện **chưa triển khai**;
   chưa có khả năng resume false interruption/semantic turn detector tương đương LiveKit.

## Phụ lục: nguyên văn đáp án của run đủ 35 câu

### Câu 1

Khách: Anh ơi, nhà em đang thiếu mấy đồ dùng cơ bản, anh tư vấn giúp em với.

Trả lời thực tế: Bạn đang cần mua những đồ dùng nào?

Model: `consultation:openai/gpt-oss-120b`. Thời gian: 7.262 s.

### Câu 2

Khách: Em muốn mua cái gì đó ăn sáng cho nhanh mà vẫn no, có loại nào ổn không?

Trả lời thực tế: Bạn có muốn mình đề xuất các sản phẩm như bánh mì, bánh bao hoặc ngũ cốc ăn sáng trong danh sách hiện có không?

Model: `consultation:openai/gpt-oss-120b:review-clarification`. Thời gian: 13.859 s.

### Câu 3

Khách: Dạo này trời nóng quá, có loại nước nào uống ngon mà không quá ngọt không?

Trả lời thực tế: Mình đang tra hàng hơi chậm. Bạn nhắn lại giúp mình yêu cầu vừa rồi nhé?

Model: `consultation:consultation-fallback`. Thời gian: 12.075 s.

### Câu 4

Khách: Nhà em có trẻ con, muốn mua ít đồ ăn vặt mà đỡ ngọt thì chọn gì nhỉ?

Trả lời thực tế: Bạn muốn chọn đồ ăn vặt dành cho trẻ bạn bao nhiêu tuổi?

Model: `consultation:openai/gpt-oss-20b`. Thời gian: 4.41 s.

### Câu 5

Khách: Em định mua quà cho mẹ mà chưa biết chọn cái gì, tầm 300 nghìn thôi.

Trả lời thực tế: Mẹ bạn thích món quà gì?

Model: `consultation:openai/gpt-oss-20b`. Thời gian: 2.562 s.

### Câu 6

Khách: Có gì ăn tiện mà không cần nấu nướng nhiều không anh?

Trả lời thực tế: Mình đang tra hàng hơi chậm. Bạn nhắn lại giúp mình yêu cầu vừa rồi nhé?

Model: `consultation:consultation-fallback`. Thời gian: 4.023 s.

### Câu 7

Khách: Em muốn mua đồ để dọn nhà cuối tuần, cần những gì nhỉ?

Trả lời thực tế: Bạn sẽ dọn nhà ở khu vực nào và muốn làm gì (lau sàn, vệ sinh bếp, giặt đồ…)?

Model: `consultation:openai/gpt-oss-120b`. Thời gian: 1.952 s.

### Câu 8

Khách: Em có khoảng 200 nghìn, mua đồ ăn đủ cho hai người một bữa tối được không?

Trả lời thực tế: Bạn muốn món ăn chính là gì, hay muốn một bữa ăn đơn giản với vài món ăn nhẹ?

Model: `consultation:openai/gpt-oss-20b`. Thời gian: 2.754 s.

### Câu 9

Khách: Có loại dầu gội nào tầm dưới 150 nghìn mà dùng ổn không?

Trả lời thực tế: Mình đang tra hàng hơi chậm. Bạn nhắn lại giúp mình yêu cầu vừa rồi nhé?

Model: `consultation:consultation-fallback`. Thời gian: 3.084 s.

### Câu 10

Khách: Em muốn mua ít đồ ăn cho cả tuần, khoảng 500 nghìn thì nên mua gì?

Trả lời thực tế: Bạn dự định cho bao nhiêu người và mỗi ngày bao nhiêu bữa ăn?

Model: `consultation:openai/gpt-oss-120b`. Thời gian: 1.904 s.

### Câu 11

Khách: Loại nào vừa rẻ vừa dùng được lâu ấy anh?

Trả lời thực tế: Bạn muốn mua loại gì và có ngân sách tối đa bao nhiêu?

Model: `consultation:openai/gpt-oss-20b`. Thời gian: 4.406 s.

### Câu 12

Khách: Cái này hơi đắt, có loại nào tương tự mà rẻ hơn không?

Trả lời thực tế: Bạn đang nói về món nào?

Model: `consultation:openai/gpt-oss-20b`. Thời gian: 3.309 s.

### Câu 13

Khách: Em không cần hàng xịn nhất đâu, loại nào đáng tiền là được.

Trả lời thực tế: Mình đang tra hàng hơi chậm. Bạn nhắn lại giúp mình yêu cầu vừa rồi nhé?

Model: `consultation:consultation-fallback`. Thời gian: 2.91 s.

### Câu 14

Khách: Hai loại sữa này khác nhau ở điểm nào vậy anh?

Trả lời thực tế: Bạn đang muốn so sánh loại sữa nào?

Model: `consultation:openai/gpt-oss-120b`. Thời gian: 3.241 s.

### Câu 15

Khách: OMO với Ariel thì loại nào giặt sạch hơn nhỉ?

Trả lời thực tế: Bạn đang muốn so sánh OMO và Ariel dạng nào?

Model: `consultation:openai/gpt-oss-120b`. Thời gian: 2.308 s.

### Câu 16

Khách: Loại này đắt hơn có đáng để mua không?

Trả lời thực tế: Bạn đang nói tới món nào?

Model: `consultation:openai/gpt-oss-20b`. Thời gian: 3.541 s.

### Câu 17

Khách: Em thấy hai cái này gần giống nhau, sao giá chênh nhiều thế?

Trả lời thực tế: Bạn đang so sánh hai sản phẩm nào?

Model: `consultation:openai/gpt-oss-120b`. Thời gian: 2.146 s.

### Câu 18

Khách: Nếu là anh thì anh chọn loại nào?

Trả lời thực tế: Được chứ. Bạn đang muốn chọn đồ ăn, đồ uống hay nhóm khác? Cho mình thêm tầm giá hoặc tiêu chí nhé.

Model: `local-clarification`. Thời gian: 2.609 s.

### Câu 19

Khách: Cùng một mức giá thì loại nào dùng thích hơn?

Trả lời thực tế: Bạn đang so sánh loại hàng nào?

Model: `consultation:openai/gpt-oss-20b`. Thời gian: 2.248 s.

### Câu 20

Khách: Da em khá nhạy cảm, có loại sữa tắm nào dịu nhẹ không?

Trả lời thực tế: Mình đang tra hàng hơi chậm. Bạn nhắn lại giúp mình yêu cầu vừa rồi nhé?

Model: `consultation:consultation-fallback`. Thời gian: 2.554 s.

### Câu 21

Khách: Mẹ em không thích đồ quá ngọt, có loại bánh nào phù hợp không?

Trả lời thực tế: Mẹ bạn muốn bánh ít đường, mình có 2 lựa chọn phù hợp: Bánh Gullon ăn kiêng không đường nhiều chất xơ 170g, 72.000đ và Bánh xốp Gullon ăn kiêng không đường 210g/180g, 112.500đ. Cả hai đều là bánh Gullon ăn kiêng, không đường và giàu chất xơ, nên vị ngọt nhẹ, không gây cảm giác ngọt quá mức. Bạn có thể thử một gói mỗi loại để so sánh cảm nhận, hoặc chọn một trong chúng tùy sở thích. Giá tham khảo; bạn kiểm tra giá và hàng tại quầy nhé.

Model: `consultation:openai/gpt-oss-120b`. Thời gian: 5.474 s.

### Câu 22

Khách: Nhà em có nuôi mèo, muốn mua đồ vệ sinh nhà mà an toàn khi dùng quanh thú cưng.

Trả lời thực tế: Bạn muốn mua loại đồ vệ sinh nào (ví dụ: nước lau sàn, nước lau kính, nước rửa chén) và có yêu cầu không dùng chất gây hại cho mèo không?

Model: `consultation:openai/gpt-oss-120b`. Thời gian: 2.259 s.

### Câu 23

Khách: Em đang hạn chế đường, có đồ uống nào phù hợp không?

Trả lời thực tế: Bạn muốn uống loại nước ngọt có ga, nước uống dinh dưỡng, hay loại nước uống khác?

Model: `consultation:openai/gpt-oss-20b`. Thời gian: 2.851 s.

### Câu 24

Khách: Em cần loại nhỏ gọn thôi, phòng em bé lắm.

Trả lời thực tế: Bạn đang tìm gì cho phòng bé?

Model: `consultation:openai/gpt-oss-120b`. Thời gian: 2.071 s.

### Câu 25

Khách: Em muốn loại không có mùi nồng, dùng hằng ngày ấy.

Trả lời thực tế: Bạn muốn dùng loại lăn khử mùi hay xịt khử mùi?

Model: `consultation:openai/gpt-oss-120b`. Thời gian: 2.444 s.

### Câu 26

Khách: Anh ơi, nước giặt ở đâu vậy?

Trả lời thực tế: Mình đang tra hàng hơi chậm. Bạn nhắn lại giúp mình yêu cầu vừa rồi nhé?

Model: `consultation:consultation-fallback`. Thời gian: 2.445 s.

### Câu 27

Khách: Có bán cái loại sữa hộp màu xanh xanh không nhỉ?

Trả lời thực tế: Bạn muốn tìm sữa hộp màu xanh xanh cho ai?

Model: `consultation:openai/gpt-oss-20b`. Thời gian: 2.21 s.

### Câu 28

Khách: Loại mì Hàn Quốc cay cay hôm trước em mua còn không?

Trả lời thực tế: Mình đang tra hàng hơi chậm. Bạn nhắn lại giúp mình yêu cầu vừa rồi nhé?

Model: `consultation:consultation-fallback`. Thời gian: 2.308 s.

### Câu 29

Khách: Em tìm mãi không thấy khăn giấy, nó nằm khu nào vậy?

Trả lời thực tế: Mình đang tra hàng hơi chậm. Bạn nhắn lại giúp mình yêu cầu vừa rồi nhé?

Model: `consultation:consultation-fallback`. Thời gian: 2.282 s.

### Câu 30

Khách: Cái loại 1 lít ấy, chứ không phải chai nhỏ đâu.

Trả lời thực tế: Bạn muốn mua loại nào 1 lít?

Model: `consultation:openai/gpt-oss-120b`. Thời gian: 2.196 s.

### Câu 31

Khách: Có sản phẩm nào tương tự không, nếu cái em cần hết hàng rồi?

Trả lời thực tế: Bạn muốn tìm sản phẩm thay thế cho cái bạn đã hết hàng, đúng không?

Model: `consultation:openai/gpt-oss-20b`. Thời gian: 2.341 s.

### Câu 32

Khách: Anh ơi, cái loại hay quảng cáo trên TikTok ấy, bên mình có không?

Trả lời thực tế: Bạn muốn tìm loại hàng nào được quảng cáo trên TikTok?

Model: `consultation:openai/gpt-oss-20b`. Thời gian: 3.328 s.

### Câu 33

Khách: Em muốn mua loại tốt tốt một chút, nhưng đừng đắt quá.

Trả lời thực tế: Bạn đang tìm sản phẩm nào cụ thể?

Model: `consultation:openai/gpt-oss-120b`. Thời gian: 2.291 s.

### Câu 34

Khách: Có cái nào dùng được cho cả nhà không?

Trả lời thực tế: Bạn muốn tìm sản phẩm nào dùng chung cho cả nhà, ví dụ như đồ dùng nhà bếp, vật dụng vệ sinh hay đồ chăm sóc cá nhân?

Model: `consultation:openai/gpt-oss-120b`. Thời gian: 1.922 s.

### Câu 35

Khách: Hôm trước em mua loại màu vàng ấy, giờ có loại nào tốt hơn không?

Trả lời thực tế: Bạn đang muốn tìm loại màu vàng nào?

Model: `consultation:openai/gpt-oss-20b`. Thời gian: 2.637 s.
