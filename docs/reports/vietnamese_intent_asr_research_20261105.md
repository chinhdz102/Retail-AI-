# Tư vấn và nhận diện tiếng Việt — nghiên cứu và kiểm tra 05–06/11/2026

## Kết luận hiện tại

**Chưa đạt chất lượng nhân viên bán hàng.** Test thật vẫn thấy mất điều kiện ở lượt tiếp nối, lời giải thích tự thêm công dụng, và fallback dịch vụ. Không lấy HTTP 200 hay số unit test để kết luận tư vấn tốt. Báo cáo này giữ cả kết quả xấu, thí nghiệm không hiệu quả và giới hạn của dữ liệu.

Trạng thái cuối 06/11: backend **2.554 passed, 1 skipped**; capture tư vấn cuối hai lượt **0 đạt, 1 yếu, 1 lỗi dịch vụ**. Đã sửa giao thức dữ kiện của reviewer, chuyển tiếp mốc so sánh là hàng khách thực sự đã xem, và giữ ngân sách/điều loại trừ qua bước phục hồi lời tư vấn. Các sửa này chưa chứng minh tư vấn đạt mức nhân viên. Endpoint âm thanh sau sửa STT trả **14/14 HTTP200**, nhưng còn lỗi giọng vùng và thương hiệu; không có bằng chứng tăng accuracy từ các cờ xác nhận. Đã thử thêm Azure fast transcription: chế độ thường gọi được, enhanced LLM bị endpoint hiện tại từ chối. Không thay model mặc định từ kiểm tra khả dụng này.

Demo: <http://127.0.0.1:5173/>; API: <http://127.0.0.1:8000/>. Kiosk cố định tiếng Việt, bỏ bộ chọn tiếng Anh. Các API ngôn ngữ cũ còn để tương thích; không dùng kết quả tiếng Anh để đánh giá đợt này.

## Nghiên cứu và quyết định triển khai

| Nguồn chính đã đọc | Ý nghĩa với dự án | Việc đã làm / giới hạn |
| --- | --- | --- |
| [RealWorldShop, EMNLP 2026](https://aclanthology.org/2026.findings-emnlp.740/) | Tư vấn cần giữ trạng thái và cập nhật điều kiện xuyên phiên; một câu nghe hợp lý chưa giải quyết được nhiệm vụ. | Giữ mục đích, ngân sách, điều loại trừ, câu đã hỏi và câu chưa xử lý; tách lập kế hoạch, tìm hàng và trả lời. Đây là vận dụng thiết kế, không phải tái lập kết quả của paper. |
| [Shopping Reasoning Bench, EMNLP 2026](https://aclanthology.org/2026.emnlp-main.1576/) | Đánh giá nhiều lượt theo tiêu chí quyết định và đánh đổi của khách. | Rubric được khai báo trước; giữ lỗi dịch vụ trong mẫu số; đọc từng câu trả lời và từng thẻ hàng. Bộ ca đã lộ, người chấm là Codex, chưa phải đánh giá độc lập. |
| [ChatCRS, NAACL 2025](https://aclanthology.org/2025.findings-naacl.17/) | Tách điều khiển mục tiêu hội thoại với truy xuất kiến thức. | Mục đích khách và giả thuyết planner không được dùng làm bằng chứng công dụng sản phẩm. |
| [Vietnamese ASR: A Revisit, EACL 2026](https://aclanthology.org/2026.findings-eacl.345/) và [PhoASR](https://github.com/qualcomm-ai-research/PhoASR) | Cần so decoder trên tiếng Việt, đa miền và giọng vùng; không chọn theo tên model. | Đã khảo sát mô hình/dữ liệu. Không đánh đồng checkpoint công bố 3.000 giờ với thí nghiệm 500 giờ trong paper; PhoASR chưa được triển khai trong demo. |
| [VLSP Zipformer, 2025](https://aclanthology.org/2025.vlsp-1.4.pdf) | Decoder tiếng Việt nhỏ có thể là đối chứng độc lập cho Whisper. | Thử Zipformer30M CPU và thêm bộ đối chứng khi bản chép bất đồng; không đổi toàn bộ decoder theo một mẫu nhỏ. Dữ liệu thi 4.000 giờ khác checkpoint ghi 6.000 giờ. |
| [How to Recognize New Words, 08/2026](https://arxiv.org/abs/2608.05759) | Hiệu quả gợi ý từ phụ thuộc giọng đọc/tự nhiên, số từ gây nhiễu và thứ tự từ. | Từ gợi ý ngắn lấy từ lời khách thật và sản phẩm đang xem; không đưa câu đáp án vào prompt. Không suy tỷ lệ cải thiện của paper thành kết quả tiếng Việt của dự án. |
| [Multipronunciation contextual biasing, 2025](https://arxiv.org/abs/2508.17796), [CB-Whisper, 2024](https://aclanthology.org/2024.lrec-main.262/) | Gợi ý dựa trên âm thanh và decoder có thể tốt hơn tự sửa câu bằng LLM. | Groq hosted không cung cấp decoder nội bộ để triển khai trọn vẹn. Chưa tuyên bố đã có acoustic keyword spotting hoặc trie biasing cho Whisper. |
| [Sherpa hotword documentation](https://k2-fsa.github.io/sherpa/onnx/hotwords/index.html) | Hotword phải đúng tokenizer BPE và decoder phù hợp. | Đã sửa lần thử token hóa sai, chạy lại BPE/modified beam search; không bật mặc định vì không cải thiện mẫu retail. |

Không có bằng chứng trong đợt này để gọi một phương pháp là “mạnh nhất” cho giọng người dùng và phòng thật.

## Thay đổi trong ứng dụng

- Planner chọn ID nhóm cụ thể trong taxonomy thật. Không nhúng câu khách hoặc mã hàng làm đáp án vào code.
- Model chọn hàng và viết lý do theo block; ứng dụng gắn đúng tên, giá, số lượng và tổng tiền. Schema kiểm ID, kiểu dữ liệu và số lượng trước khi render. Sửa lỗi schema không có nghĩa duyệt nội dung.
- Lượt nhờ tư vấn tiếp dùng lại ngữ cảnh đã chấp nhận. Khi chưa có hàng đã hiển thị và yêu cầu trước chưa xử lý xong, tiếp tục hướng tư vấn thay vì tự ép thành một lần chọn mỹ phẩm/SKU mới.
- Tư vấn cách chọn được phép dùng kiến thức mua sắm thông thường. Nguồn hướng dẫn chung không chứng minh hiệu năng, độ dịu, khẩu phần, độ mềm hoặc khả năng điều trị của một SKU.
- Biên tập giữ lựa chọn/số lượng đã đúng, sửa lỗi cụ thể. Bản sửa bị bác không được xuất ra chỉ vì nghe có ích. Vẫn có lỗi ngữ nghĩa lọt qua reviewer trong test thật.
- Bổ sung nguồn AAD về [làm sạch cho da khô/nhạy cảm](https://www.aad.org/public/everyday-care/skin-care-basics/dry/dermatologists-tips-relieve-dry-skin): tiêu chí dịu nhẹ, nhãn fragrance-free; không suy từ thương hiệu. Không tạo thông tin nhãn cho SKU đang thiếu dữ liệu.
- Thử phục hồi tư vấn chung khi chọn hàng thất bại: sinh lời mới và duyệt độc lập, không xuất nháp đã bị bác. Loại các miền sức khỏe, hóa chất, trẻ nhỏ, tương thích, giỏ nhiều món và yêu cầu nhãn khỏi bước này. Bước phục hồi dùng phần còn lại của ngân sách thời gian 32 giây, tối đa 8 giây; không có bằng chứng nó giải quyết hết các ca lỗi.
- STT tiếp tục dùng **Whisper Large v3**; turbo chỉ là dự phòng khi provider lỗi. Kiosk gửi `vi`, không dùng nhận diện tự chọn ngôn ngữ.
- Từ gợi ý STT ưu tiên thương hiệu thật khách đã nhắc trong hai lượt gần nhất, rồi hàng đang xem, cuối cùng từ phổ biến. Tác vụ đọc catalog có deadline riêng.
- Zipformer là đối chứng bất đồng, không thay câu Whisper bằng câu đoán. Khác từ phủ định hoặc phạm vi phủ định có thể đổi ý mua nên yêu cầu xem lại. Chữ số/cách đọc số tương đương không tự gây bất đồng.
- Khi cần xem lại bản chép, giao diện cho sửa rồi gửi; không tự gọi tư vấn/TTS từ bản chép đó.

## Kết quả tư vấn qua HTTP thật

Các lượt gọi `/agent/respond` dùng phiên thật, có khoảng nghỉ giữa lượt. Không sửa source giữa một capture hoàn chỉnh. Hash trong artifact là hash file; việc restart dịch vụ được thực hiện riêng, không giả định hash chứng minh phiên bản process.

| Capture | Phạm vi | Kết quả |
| --- | --- | --- |
| `vietnamese_intent_core_api_20261105.json` | Harness đầu xử lý sai HTTP 201 khi tạo session | **Không hợp lệ để chấm tư vấn**: không có lượt tư vấn nào thực sự được gọi. |
| `..._v2.json` | 17 lượt, 10 hành trình | Codex chấm: **3 đạt, 3 yếu, 4 sai, 7 lỗi dịch vụ**. |
| `..._v3.json`, `..._v4.json` | Bị dừng do source thay đổi | Chỉ là capture một phần, không so như bộ hoàn chỉnh. v4 có 3 lượt thử, 14 lượt chưa chạy. |
| `..._v5.json` | 17/17 lượt, source không đổi | Codex chấm: **1 đạt, 3 yếu, 7 sai, 6 lỗi dịch vụ**. Chưa có bằng chứng cải thiện tổng thể so v2; không so thống kê từ một lần gọi mỗi ca. |

Các ca v5 sai gồm: tự đặt lịch gội và hiệu quả làm sạch; suy hiệu năng bột/nước giặt; tiêu chí an toàn cho mèo ngoài nguồn; khẳng định gáy keo mở phẳng; gọi I-On Life là nước khoáng; chuyển câu điều trị mụn thành gợi ý mỹ phẩm kèm tần suất dùng. Ba lượt lẩu đều fallback. Ca sổ đầu chỉ đúng tên/giá; ca đồ uống Zero đạt rubric. [Bản chấm v5](artifacts/vietnamese_intent_core_api_20261105_v5_manual.json) có từng câu gốc, câu trả lời và lý do.

**Capture tập trung sau sửa cuối**: `vietnamese_intent_recovery_api_20261105.json` gồm 11 lượt về da nhạy cảm, lẩu, sổ, mụn và đổi chai nước rửa chén nhỏ hơn. Kết quả cuối và lần xác minh mã được bổ sung ở phần cuối báo cáo.

Đã thử trực tiếp UI hai lượt nước rửa chén: lượt đầu chọn Sunlight nhưng tự thêm công dụng chanh; lượt đổi chai nhỏ hơn fallback. Đây là lỗi thực tế đã quan sát, không ghi là UI tư vấn đạt. Kiosk tiếng Việt và nhập text hoạt động; chưa có kiểm tra phát âm qua microphone vật lý trong thao tác UI đó.

## STT: đối chứng cùng file âm thanh

33 file được lên lịch: 22 bản người thật, 6 câu retail tổng hợp Azure, 5 control môi trường ESC-50. Bốn file ViVoice dài hơn giới hạn 60 giây bị từ chối ở tất cả cấu hình; báo riêng, không coi là nhận đúng. Có 18 file người thật hợp lệ để tính bảng dưới.

WER là lỗi trên từ tách bằng khoảng trắng, nhạy với cách viết số, dấu và thương hiệu. Tập nhỏ, đã lộ, có thể trùng dữ liệu huấn luyện; không đại diện giọng nói trong phòng người dùng.

| Nhóm | Mẫu hợp lệ | Whisper hiện tại | Whisper prompt rút gọn | Zipformer | Zipformer + BPE hotwords |
| --- | ---: | ---: | ---: | ---: | ---: |
| FLEURS | 12, 351 từ | 7,41% | 6,84% | 5,70% | 5,70% |
| ViVoice | 3/7, 460 từ | 22,61% | 45,87% | 10,87% | 10,65% |
| ViMD | 3, 52 từ | 13,46% | 15,38% | 13,46% | 13,46% |
| Retail tổng hợp | 6, 74 từ | 13,51% | 13,51% | 4,05% | 4,05% |

Không bật prompt rút gọn vì làm mất nhiều nội dung file dài. Hotwords đúng BPE không cải thiện retail trong mẫu này; lần hotword token hóa sai không dùng để kết luận hiệu quả. Không mặc định thay Whisper toàn cục từ các số trên.

Endpoint thật `/audio/transcribe`: 14/14 HTTP 200, gồm 9 file lời nói và 5 control không lời. Control trả rỗng/không nhận là tiếng nói. Các câu phủ định thịt heo, ngân sách 150 nghìn/không sữa tắm, chai một lít/không 100 ml được giữ trong mẫu. Vẫn sai “mùi nồng” thành “mùi nông”; trường hợp này được gắn yêu cầu xem lại. Mẫu Nghệ An vẫn sai đáng kể và được gắn xem lại. Câu OMO/Ariel vẫn ra A-RIN khi chưa có ngữ cảnh, không được cờ bất đồng bắt hết.

Đối chứng ngữ cảnh trên **cùng một clip tổng hợp**, cùng hash: phiên mới chép OEMO/A-RIN; sau lời text thật của khách nêu “nước giặt OMO với Ariel”, Whisper chép đúng OMO/ARIEL ngay ở raw output. Không đưa câu tham chiếu vào prompt. Đây là một ví dụ cải thiện có quan sát, không phải tỷ lệ chính xác mới. [Artifact ngữ cảnh](artifacts/session_speech_context_20261105.json).

Heuristic bất đồng gắn cờ 3/24 file lời nói hợp lệ trong replay. Đây là tần suất yêu cầu xem lại trên mẫu, không phải xác suất đúng hoặc mức phát hiện mọi lỗi.

TTS API trả HTTP 200 với Azure `vi-VN-HoaiMyNeural`, 30.528 byte, 1,446 giây trong một lần gọi. Không suy chất lượng nghe, phát âm hoặc khả năng ngắt lời từ việc nhận được byte âm thanh.

Checkpoint Zipformer30M đã thử có giấy phép **CC-BY-NC-ND 4.0**. Đợt này là chẩn đoán đồ án; không chứng minh có thể triển khai thương mại. Kiosk hiện không chuyển sang runtime LiveKit; test file không đi qua microphone, browser VAD, loa, echo hoặc phòng thật.

## Thí nghiệm không đạt và phần cần làm tiếp

Tăng reasoning lên medium, đổi prompt sang tiếng Anh và nhắc lỗi sát câu khách chưa cho cải thiện đủ tin cậy. Thử phục hồi tư vấn mụn sinh thêm hoạt chất ngoài nguồn và reviewer cho qua; vì vậy không cho phục hồi tổng quát áp dụng vào miền đó. Không gọi các thử nghiệm này là tối ưu thành công.

Việc tiếp theo có giá trị hơn việc nối thêm một câu cấm vào prompt:

1. Chấm độc lập các hội thoại chưa lộ bằng tiêu chí giữ ý, quyết định có ích, không bịa công dụng; tách lỗi planner, retrieval, realization, review và provider. Dữ liệu nhân viên viết/chỉnh được duyệt có thể làm dữ liệu preference/SFT; chưa huấn luyện hay gọi dữ liệu do model tự viết là nhãn người thật.
2. Thử verifier theo từng phát biểu và nguồn chứng minh, so với reviewer hiện tại trên cả câu đúng và câu sai. Chỉ đưa vào runtime nếu giảm lỗi mà không chặn lời khuyên thông thường hoặc tăng fallback quá nhiều.
3. Kiểm tra kích thước tương đối/loại trừ trên hàng thật và khả năng hội tụ về một lựa chọn. Không tạo luật riêng cho thịt lẩu, sổ hay nước rửa chén.
4. Thu bộ âm thanh có đồng ý: nhiều người nói tiếng Việt, giọng vùng, câu tự nhiên, quạt/điều hòa/máy tính, im lặng và tiếng loa. Chấm WER/CER, sai phủ định/tiền/đơn vị/nhãn hiệu, bỏ sót lượt và nhận nhầm lúc im lặng.
5. So Whisper/PhoASR/Zipformer trên cùng âm thanh, ngữ cảnh và timeout. Đo chất lượng cả microphone → VAD → STT → sửa/xác nhận → tư vấn → TTS, bao gồm cắt đầu/cuối câu, echo, ngắt lời và reconnect. Việc này chưa hoàn thành bằng các replay file hiện có.

## Cập nhật 06/11/2026: sửa lỗi điều kiện và giới hạn của verifier

Đã đọc thêm phần phương pháp M1–M4 và Appendix E của RealWorldShop, cùng [PACEShop/PACEJudge](https://aclanthology.org/2026.findings-emnlp.2121.pdf), [FActScore](https://aclanthology.org/2023.emnlp-main.741/) và [Verify with Caution](https://aclanthology.org/2025.findings-acl.1175/). Vận dụng kiểm từng phát biểu và chỉ ra chỗ sai; không tái lập đầy đủ paper. RealWorldShop dùng cả SFT/RL và bộ điều khiển phiên; dự án này chưa huấn luyện SFT/RL nên không nhận kết quả của paper làm kết quả của mình.

Bộ duyệt mới kiểm **bản đang xuất**, mỗi câu có một verdict và nguồn trích tồn tại thật. Không cho reviewer tự viết lại rồi chấm chính bản sửa đó. Tuy nhiên, trích dẫn tồn tại không chứng minh câu được nguồn hỗ trợ; model vẫn có thể gắn nhãn suggestion cho lời hứa sai. Trong capture 11 lượt `vietnamese_intent_verified_api_20261105.json`, Codex chấm **0 đạt, 2 yếu, 3 sai, 6 lỗi dịch vụ**. Các lỗi đã lọt qua gồm khẩu phần/nhiệt độ tự thêm; sổ lò co bị gọi là không lò xo; khối lượng 750g bị gọi là dung tích. Vì thế verifier này chưa chứng minh cải thiện chất lượng so bộ cũ.

Capture tập trung trước đó `vietnamese_intent_recovery_api_20261105.json` có **1 đạt, 2 yếu, 3 sai, 5 lỗi dịch vụ**. Các bản chấm có hậu tố `_manual.json`; tất cả là đánh giá thủ công của Codex theo rubric đã khai báo, chưa phải người dùng/nhân viên chấm độc lập.

Các sửa tiếp theo ngày 06/11:

- Hàng đã hiện không còn đi vòng qua bộ lọc của vai trò tìm kiếm mới. Giá, nhóm hàng và điều loại trừ được kiểm lại trước khi đưa vào candidates.
- Chuẩn hóa một số cách viết/dạng vật lý tương đương như cắt lát/thái lát, băm/xay, túi châm/refill và lò co/lò xo trong sổ/vở. Dùng ranh giới từ và phạm vi phủ định; không suy vắng chữ là không có thành phần. Giữ dấu để không nhập nhằng bò/bơ. Đây là từ vựng thuộc tính, không có câu hỏi→SKU hay câu trả lời riêng cho ca thử.
- Lượt chỉ cần hướng dẫn không truy xuất/đưa SKU không thể chọn vào prompt. Nguồn hướng dẫn và mục đích khách được giữ; wire không cho block tham chiếu một candidate khi `max_picks=0`.
- Bổ sung kiểm tra số đo, nhiệt độ, khẩu phần/tần suất có con số ngoài nguồn sản phẩm/hướng dẫn. Mong muốn của khách hoặc nhãn suggestion của model không tự trở thành bằng chứng. Kiểm này chỉ là hỗ trợ từ vựng; chưa chứng minh entailment, chưa bao phủ mọi số viết bằng chữ hay quan hệ suy luận.
- STT kiểm thêm bất đồng tiền/đơn vị/số lượng và bất đồng lớn giữa hai decoder, kể cả khi Whisper báo logprob cao. Cách viết tương đương 1 lít/1000 ml hoặc 150 nghìn/một trăm năm mươi ngàn không tự tạo bất đồng. Không chọn thay bản chép bằng một phỏng đoán. Replay 24 cặp đã ghi vẫn có 3 cờ trước và sau; đây là regression chứ không phải tăng độ chính xác ASR.

Full backend sau các sửa trên: **2.523 passed, 1 skipped** (296,21 giây), artifact `backend_vietnamese_constraints_20261106.xml`. Skip là quyền tạo symlink trên Windows. Bộ trước đó 2.486 passed/1 skipped được giữ làm lịch sử. Frontend không đổi trong lượt sửa cuối: **121/121 test và build thành công** ở artifact `frontend_vietnamese_intent_20261105_final.json`. Ruff trên các module mới liên quan và `git diff --check` qua; không lấy số test làm điểm chất lượng nhân viên.

## Kiểm tra âm thanh qua endpoint ngày 06/11

`vietnamese_audio_api_20261106.json`: 14 file, **13 HTTP200, 1 HTTP504**, source không đổi. Clip Nghệ An dài khoảng47 giây timeout ở ngân sách chung20 giây; không loại khỏi mẫu số. Tám file tiếng nói còn lại trả bản chép và năm control trả rỗng. Vẫn sai tên Ariel thành A-RIN và không được cờ bắt; “mùi nồng” thành “mùi nông” được yêu cầu xem lại. TTS Azure HoaiMy trả200, 30.528byte, 1,465giây. Không có kiểm tra mic vật lý/loa/echo/browserVAD/LiveKit trong replay này.

## Xác minh cuối

### Đối chứng PhoASR đã hoàn tất

Chạy đủ 33 file cũ, cùng hash, với checkpoint chính chủ `Qualcomm-AI-Research/PhoASR-whisper-small`, revision `da2d9ae0ac0a6a6faba20add61c2e980ccd85037`. Chuyển sang CTranslate2 int8, CPU bốn luồng, beam 5, temperature 0, `without_timestamps=True`; không thực thi Python từ kho model, không đưa transcript tham chiếu vào prompt. Đây là cấu hình CPU chẩn đoán, khác decoder/hardware của Whisper hosted.

| Nhóm | Số file hợp lệ / từ tham chiếu | Whisper Large v3 | Zipformer30M | PhoASR CPU int8 |
| --- | ---: | ---: | ---: | ---: |
| FLEURS | 12 / 351 | 7,41% | 5,70% | 6,27% |
| ViVoice | 3 / 460 | 22,61% | 10,87% | 23,70% |
| ViMD | 3 / 52 | 13,46% | 13,46% | 19,23% |
| Người thật gộp | 18 / 863 | 15,87% | 8,92% | 16,34% |
| Retail tổng hợp | 6 / 74 | 13,51% | 4,05% | 9,46% |

Đây là WER có trọng số theo số từ, không phải trung bình phần trăm từng clip. Bốn file quá 60 giây bị giới hạn đầu vào ở mọi cấu hình; vẫn giữ trong lịch 33 file nhưng không tính là nhận đúng. Năm control đều rỗng ở cả ba biến thể nhờ cùng acoustic gate, không chứng minh riêng decoder nhận diện im lặng tốt. PhoASR trung vị khoảng 3,07 giây/file người thật, trong khi baseline hosted khoảng 1,03 giây và Zipformer khoảng 0,75 giây; không phải phép so hardware ngang nhau. Tập công khai nhỏ và đã lộ, có thể trùng huấn luyện; kết quả Zipformer tốt hơn trên tập này chưa đủ để gọi mạnh nhất hoặc thay mặc định cho giọng khách thật.

PhoASR vẫn đọc sai thương hiệu trong câu retail thành “OTO ... Arin”. Không đổi mặc định sang PhoASR. Hai lần chuẩn bị thất bại được giữ: artifact không hậu tố là lỗi setup, `_v2` chỉ sinh từ đầu do cấu hình timestamp, nên **không dùng chúng chấm chất lượng model**. Dùng [raw v3](artifacts/phoasr_cpu_paired_20261106_v3.json) và [tổng hợp cuối](artifacts/phoasr_cpu_summary_20261106_final.json). Bản tổng hợp đầu có thời gian trung bình gộp cả input error, không dùng để so latency; bản cuối chỉ tính file thành công. Giấy phép checkpoint gồm BSD-3-Clause-Clear và Qualcomm Responsible AI; đợt này là nghiên cứu/đồ án.

### Sửa tiếp tư vấn theo lỗi đã quan sát

Capture 11 lượt sau sửa điều kiện, trước sửa phạm vi lời tư vấn: **0 đạt, 1 yếu, 1 sai, 9 lỗi dịch vụ**, chấm theo rubric đã có. Lượt sổ chỉ đọc tên/giá; nước rửa chén gọi 750g là dung tích và hứa dùng lâu dài. Không gọi việc sổ lò co không còn hiện sai là tư vấn thành công khi khách chỉ nhận fallback. [Bản chấm đủ 11 lượt](artifacts/vietnamese_intent_constraints_api_20261106_manual.json).

Trace mụn chỉ ra lời “đi kèm điều trị” bị coi nhầm là hứa hiệu quả. Đã cho phép một số tham chiếu danh từ hẹp tới điều trị trong lời khuyên chung, đồng thời giữ test chặn lời khẳng định SKU điều trị và các mệnh đề hứa hiệu quả nối sau cảnh báo. Scoped 314 passed; full 2.523 ở trên diễn ra trước bốn test mới này. Trace thật sau đó có một lượt trả lời hợp lệ và được verifier chấp nhận; tuy nhiên probe HTTP hai lượt cùng chủ đề vẫn fallback 2/2, nên chưa có bằng chứng sửa xong ca này trên dịch vụ.

Trace thịt cho thấy lỗi khác: bản đầu bịa độ mềm, vân mỡ, vị ngọt; bản sửa bỏ các tính chất đó nhưng reviewer tiếp tục bác cả đề xuất cắt lát để nấu. Đã tách câu tên/giá do ứng dụng gắn khỏi câu tư vấn của model, và làm rõ trong reviewer rằng thao tác khách có thể thực hiện với đúng loại nguyên liệu/đồ vật là đề xuất thông thường. Ví dụ kiểm ranh giới dùng khăn, giấy/bìa, không có mapping câu hỏi→mã hàng. Verifier vẫn phải bác công dụng, số đo sử dụng và tính an toàn ngoài nguồn. Bổ sung kiểm tra chiều đo: không cho gọi g/kg là dung tích hay ml/lít là khối lượng, kể cả model duyệt nhầm. Scoped sau sửa: 343 passed.

Đọc bổ sung [Shopping Companion, EMNLP 2026](https://aclanthology.org/2026.findings-emnlp.1669.pdf) về lỗi sở thích tự bịa và thiếu đối chiếu thuộc tính, cùng [Small Agents, Big Gains, ACL Industry 2026](https://aclanthology.org/2026.acl-industry.39.pdf) về dữ liệu hành trình và phản hồi chỉ rõ lỗi. Áp dụng cách phân loại lỗi và giữ cả lượt bác/lượt sửa trong artifact. Chưa huấn luyện tool-wise rewards, SFT hay distillation; không lấy kết quả nhỏ hơn/nhanh hơn của paper làm kết quả dự án. Không tạo đội agent hay simulator mới trong lượt này.

### Sửa tiếp STT và kết quả endpoint

Optional Zipformer có tối đa bốn giây, chia sẻ phần thời gian còn lại trong deadline STT 20 giây và chừa 250ms để trả kết quả. Bận/không khả dụng/timeout thì giữ bản chép Whisper, gắn `needs_confirmation=true` để khách sửa và chủ động gửi. Không coi thiếu đối chứng là đã đồng thuận; hủy await không giải phóng slot native khi worker chưa thoát. Đã kiểm bằng test worker thực bị chặn bởi event, không chỉ mock timeout.

Bổ sung so tên thương hiệu thật trong catalog giữa hai decoder. Nếu tên xuất hiện chính xác ở một bên mà bên kia khác, yêu cầu xem lại; không tự thay lời khách bằng tên trong DB. Danh sách so không được đưa thành câu đáp án vào prompt. Replay 24 cặp cũ vẫn **3 cờ trước, 3 cờ sau**, không có quan sát tăng độ chính xác. Cả hai decoder đều đọc sai Ariel, nên quy tắc tên chính xác vẫn bỏ sót. Giữ rõ giới hạn này thay vì tuyên bố đã sửa nhận diện thương hiệu.

[Capture endpoint sau sửa](artifacts/vietnamese_audio_api_budget_identity_20261106.json) hoàn tất **14/14 HTTP200**, source không đổi; năm control trả rỗng. File Nghệ An khoảng 47 giây trả trong 12,711 giây và được yêu cầu xem lại, vẫn sai nhiều từ (WER 32,68%). “Mùi nồng” vẫn bị chép “mùi nông”, có cờ. Ariel vẫn sai và chưa có cờ. Không suy cải thiện accuracy hoặc quy hết timeout cũ cho verifier: tải CPU/lần khởi động khác nhau giữa các run. TTS Azure HoaiMy trả 200, 30.528 byte trong 1,206 giây; chưa chấm nghe qua loa.

Full backend sau các sửa cuối ở trên: **2.548 passed, 1 skipped** trong 268,18 giây, artifact `backend_vietnamese_advice_asr_20261106_final.xml`. Skip vẫn là quyền symlink Windows. Frontend không đổi so lần 121 passed/build thành công. Ruff trên các module/helper/test mới liên quan qua. Unit/integration test kiểm hành vi phần mềm; chúng không thay cho điểm tư vấn hoặc mic vật lý.

Capture bảy lượt sau sửa phạm vi lời khuyên: **0 đạt, 1 yếu, 6 lỗi dịch vụ**, source không đổi, trung vị 11,134 giây/lượt. Lượt thứ ba về thịt trả hướng mua bò/ba chỉ và cắt miếng, nhưng chủ yếu nhắc lại lựa chọn khách đã nêu, thêm lời nhiệt độ không giúp quyết định mua. Không chấm đạt. [Bảy câu trả lời và bản chấm](artifacts/vietnamese_advice_actions_api_20261106_manual.json).

### Phát hiện tiếp: bộ duyệt tự làm hỏng trích dẫn

Trace có nguồn cố định `intent_advice_boundaries_trace_20261106.json` cho thấy bản chọn đã được sửa thành lời khuyên bình thường, reviewer cũng chấp nhận, nhưng nó tái tạo một đoạn JSON với thứ tự key khác nguồn thật. Bộ kiểm trích dẫn literal vì vậy bác toàn bộ reply. Đây là lỗi giao thức dữ kiện, khác lỗi hiểu nhu cầu hoặc lỗi quota. Trace này bật Python exception tracing để tìm nguyên nhân, **không dùng thời gian của nó để benchmark latency**.

Đã chuyển nguồn sản phẩm sang dữ kiện có ID riêng (`name`, `price_vnd`, `type`, `uom`, bằng chứng thuộc tính/dạng hàng). Reviewer chọn các ID có trong enum; ứng dụng giữ nguyên giá trị nguồn thay vì yêu cầu model gõ lại đoạn JSON. Vẫn kiểm đủ statement, đúng tập sản phẩm, ID lạ/trùng, ràng buộc mua hàng và phát biểu sai; chọn một ID đúng vẫn chưa chứng minh quan hệ ngữ nghĩa đúng. Thay đổi này giảm một nguyên nhân chặn nhầm, không phải bằng chứng verifier đã đáng tin trên mọi công dụng.

Scoped sau đổi giao thức: **326 passed**, artifact `intent_fact_references_20261106.xml`. Đã đóng băng bốn nháp thật trước khi gọi reviewer: bản sửa nước rửa chén, bản bịa độ phổ biến/dùng thoải mái, một lựa chọn thịt kèm đề xuất cắt lát, và lời chăm sóc/điều trị chung. Nhãn kỳ vọng không đưa vào prompt. Đây là kiểm riêng reviewer trên dữ liệu đã lộ, không phải bốn hội thoại mới hoặc người dùng chấm độc lập.

[Probe bốn bản nháp](artifacts/intent_fact_references_probe_20261106.json) hoàn tất, source không đổi, **4/4 đúng nhãn kỳ vọng**: chấp nhận ba bản có cơ sở, bác bản bịa độ phổ biến/dùng thoải mái. Có fallback model và lỗi 429 trong trace; không gọi đây là kiểm chất lượng không phụ thuộc provider. Full backend ngay sau thay giao thức: **2.551 passed, 1 skipped**, 270,56 giây, artifact `backend_vietnamese_fact_refs_20261106.xml`.

### Mốc so sánh và ràng buộc trong bước phục hồi

Sau restart, [capture HTTP hai lượt](artifacts/vietnamese_advice_fact_refs_api_20261106.json) có **0 đạt, 0 yếu, 1 sai, 1 lỗi dịch vụ**. Lượt đầu fallback; lượt sau chọn chai 750g rồi khuyên khách sang chai riêng thay cho việc tìm chai nhỏ hơn, dù chưa có món được giới thiệu thành công ở lượt trước. Bộ duyệt theo từng câu vẫn bỏ sót việc toàn câu trả lời chưa giải quyết yêu cầu. [Bản chấm](artifacts/vietnamese_advice_fact_refs_api_20261106_manual.json).

Đã sửa tiếp theo cơ chế chung, không theo mã hàng: đưa `previously_shown_products` và trạng thái yêu cầu chưa được trả lời từ phiên vào bước chọn và reviewer. Planner đã có `visible`; nay được nhắc rõ đó là hàng đã hiện thật. Candidate mới tìm hoặc nháp bị bác không được coi là mốc khách đã thấy. Reviewer nhận dữ kiện mốc cũ qua ID `vN.*`, tách khỏi dữ kiện sản phẩm đang chọn `cN.*`. Khi thiếu mốc cho yêu cầu tương đối, cần làm rõ một lần; không giả vờ món mới nhỏ/rẻ hơn một món chưa xác định. Đây là bổ sung thông tin và policy, chưa phải bộ chứng minh mọi phép so sánh.

Phát hiện thêm đường phục hồi lời tư vấn chỉ đưa `action=advise` vào bộ duyệt, làm rơi ngân sách và điều loại trừ; renderer cũng dùng plan mới không có ngân sách. Đã giữ bản plan có ràng buộc khi chuyển sang lời khuyên chung. Bài test kiểm cả việc nhắc lại đúng ngân sách được chấp nhận và ngân sách/điều loại trừ thực sự tới reviewer. Không dùng lời khách làm chứng cứ cho công dụng sản phẩm.

Scoped sau các sửa này: **94 passed**. Full backend cuối: **2.554 passed, 1 skipped**, 270,39 giây, [JUnit](artifacts/backend_vietnamese_reference_final_20261106.xml). Ruff các module/harness/test liên quan qua. Frontend không đổi so lần **121 passed/build thành công**.

Restart và [capture HTTP cuối hai lượt](artifacts/vietnamese_advice_reference_final_api_20261106.json): **0 đạt, 1 yếu, 1 lỗi dịch vụ**, source không đổi. Lượt đầu chọn đúng nước rửa chén và giá dưới ngân sách, nhưng lời giải thích chỉ lặp chức năng và mức tiền. Lượt đổi chai nhỏ hơn fallback; log có cả cooldown/429 và lỗi schema, nên không quy hết cho hiểu ý hoặc cho quota. [Bản chấm từng câu](artifacts/vietnamese_advice_reference_final_api_20261106_manual.json). Không có bằng chứng đủ để kết luận sửa xong yêu cầu đổi quy cách, và không so thống kê trước/sau từ hai lần gọi model ngẫu nhiên.

### Khảo sát STT mới: Azure fast transcription

Đọc thêm [thông báo LLM Speech 09-2026 ngày 26/10](https://devblogs.microsoft.com/foundry/announcing-azure-ai-speech-llm-09-2026/), [hướng dẫn fast transcription](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/fast-transcription-create), [API 2025-10-15](https://learn.microsoft.com/en-us/rest/api/speechtotext/transcriptions/transcribe?view=rest-speechtotext-2025-10-15) và [bảng hỗ trợ tiếng Việt](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=stt). Đây là tài liệu kỹ thuật nhà cung cấp, khác với paper có đánh giá độc lập. Tài liệu overview vẫn ghi bản 07-2026 trong khi blog công bố 09-2026; version được dịch vụ tự quản lý, không thể suy phiên bản đang phục vụ từ tên API.

Probe khả dụng dùng một file FLEURS công khai đã có, giữ hash, `locales=[vi-VN]`, không prompt tham chiếu, không phrase list. Enhanced trả **HTTP400 / InvalidRequest / “Enhanced mode is currently not supported yet.”** ở endpoint hiện tại. Chế độ fast thường trả **HTTP200** và bản chép vẫn có từ sai. Một lần thử enhanced trước khi thêm harness cũng trả400 nhưng không lưu payload chi tiết; không dùng làm mẫu benchmark. [Artifact probe có lưu kết quả](artifacts/azure_vietnamese_availability_20261106.json) chỉ chứng minh khả dụng hai cấu hình, **không phải benchmark accuracy**. Chưa tích hợp hoặc đổi mặc định sang Azure STT, chưa giả định mọi tài khoản/region gọi được chế độ mới.

### Điều còn chưa đạt

1. Tư vấn vẫn có câu đúng thông tin nhưng nghèo lý do chọn; phát hiện lỗi từng câu chưa bảo đảm cả câu trả lời giúp quyết định mua. Cần dữ liệu hành trình được người bán/người dùng chấm độc lập và các lý do chọn có nguồn theo ngành hàng, thay vì tiếp tục thêm mẫu câu cho vài ca đã lộ.
2. Phải giảm lỗi schema và số lời gọi trên mỗi lượt, đồng thời kiểm theo retry/cooldown thật của provider. Các capture đang có chưa chứng minh khả năng tư vấn ổn định khi tải hoặc quota thay đổi.
3. STT chưa nhận đúng hết thương hiệu và giọng vùng. PhoASR/Zipformer/public-file replay chưa thay cho tập giọng khách mới, nhiều người nói, qua mic và tiếng quạt thực tế. Không đưa LLM sửa thành câu đoán rồi gọi đó là tăng độ chính xác.
4. Chưa kiểm tra mic vật lý, loa/echo và hội thoại ngắt lời trong phòng người dùng; dự án hiện không có đường LiveKit để tuyên bố đã kiểm end-to-end LiveKit.
