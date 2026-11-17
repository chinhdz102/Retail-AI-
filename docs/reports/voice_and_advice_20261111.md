# Kiểm tra STT tiếng Việt, ngắt câu, lọc ồn và tư vấn — 11/11/2026

## Kết luận hiện tại

**Chưa đạt tiêu chí “nhân viên thực sự” và chưa thể khẳng định hết lỗi STT.**
Đã sửa các lỗi code có phép tái hiện, đối chiếu âm thanh qua trình duyệt, thử
decoder/khử ồn khác và thêm bước kiểm tra lý lẽ tư vấn. Vẫn có từ bị nhận sai,
chưa có phép thử mic/loa vật lý của người dùng, và hạn mức Groq đang chặn phần
lớn lượt kiểm định hội thoại sau sửa. Không lấy HTTP 200, unit test hoặc lời
đánh giá của chính LLM làm bằng chứng đã đạt toàn bộ yêu cầu.

Demo hiện chạy Azure STT `conversation`, `vi-VN`, không prompt và không sửa
transcript. Groq `whisper-large-v3` vẫn là lựa chọn cấu hình. File sản phẩm cũ
`data/winmart_products.json` được giữ nguyên: 3.099 dòng, 3.081 hợp lệ, 18 cách
ly; SHA256 `a0eb16ac1ae2557ea8c272c680a1f17d360934da6138585e09314df5d04f19d6`.

## 1. Nguyên nhân code đã tái hiện và sửa

| Lỗi | Trước sửa | Thay đổi và bằng chứng |
|---|---|---|
| Kết thúc câu quá sớm | Nghỉ khoảng 1,1 giây rồi nói “Không đường” bị mất phần sửa ý, 4/4 lượt | Redemption 900 → 1440 ms, prepad 640 ms; Chrome sau sửa giữ cả hai vế 4/4 lượt |
| Bỏ cả câu dài | Hết 45 giây thì hủy | Flush phần đã thu; Stop chủ động vẫn hủy lượt |
| Tiếng động phủ quyết lời nói ở thời điểm khác | Bộ sự kiện âm thanh loại 2 ca lời nhỏ trong tập mixed events | So speech/other trong cùng cửa sổ; sau sửa giữ 16/16 ca thử |
| Quét thiết bị trả muộn | Có thể đổi/restart mic khi người dùng đang nói | Giữ lựa chọn mic và phiên thu hiện tại; có hồi quy tích hợp giao diện |
| Lời nhỏ chưa tới được bước phục hồi mức âm | Phát hiện phía browser có thể không kích hoạt | Silero có hai trạng thái độc lập cho âm gốc/âm tăng mức; âm gửi vẫn lấy từ PCM gốc |
| Chồng điều chỉnh mức âm | Browser AGC cộng với các bước tăng mức khác | Tắt browser AGC; chỉ tăng file rất nhỏ sau xác minh có tiếng nói, có giới hạn gain/peak |

Hàng đợi frame tăng từ 12 lên 64, quá tải báo lỗi thay vì bỏ frame âm thầm.
Đây là phòng ngừa có kiểm tra code, **chưa chứng minh queue overrun gây lỗi
người dùng đã gặp**. Không gán mọi lỗi dấu cho ngắt câu hay cho model.

Bằng chứng:
[trước ngắt câu](artifacts/voice_pause_before_20261110.json),
[sau ngắt câu](artifacts/voice_pause_after_20261110.json),
[Chrome + Azure](artifacts/voice_pause_azure_20261111.json),
[mixed events trước](artifacts/vad_mixed_events_before_20261110.json),
[mixed events sau](artifacts/vad_mixed_events_after_20261110.json).

## 2. STT thật và bảo toàn âm thanh

| Phép thử | Kết quả | Giới hạn |
|---|---|---|
| Pipeline Azure, 90 lượt decode/kiểm tra acoustic | 48/48 câu sạch thường/nhỏ có lời; 12/12 câu có quạt có lời; 24/24 FLEURS thường/nhỏ có lời; 6/6 không lời trả rỗng; 0 lỗi API | “Có lời” không có nghĩa đúng chữ |
| 60 lượt HTTP: môi trường + câu ngắn | 53/53 không lời rỗng; 6/7 câu ngắn đúng | “Sữa” vẫn thành “Sợ” |
| Chrome, Có/Không/Dạ thường/nhỏ, production/raw | 12/12 đúng, không lỗi capture/API | Mic giả, giọng tổng hợp |
| Chrome, 12 lượt câu nhỏ/quạt | 8/8 câu nhỏ lẩu/sàn đúng; thịt tươi với giọng Nam Minh đúng 2/2; Hoài My có quạt sai 2/2 | “thịt tươi” → “thực tư” ở cả production và raw |
| So worklet frames với WAV của 12 lượt trên | Năng lượng giữ lại tối thiểu xấp xỉ 100%; cosine tối thiểu 0,9999648; track đều đóng | Chứng minh đoạn đã thu không bị DSP cắt/hỏng, không chứng minh mọi mic/phòng |
| 6 lượt HTTP đối chiếu dấu từ provider → API | 6/6 giữ nguyên; 6/6 khớp câu lẩu/sàn tham chiếu, gồm mẫu ồn | Không tái hiện được cặp riêng “lẩu → lấu” của người dùng |

Artifacts:
[90 lượt pipeline](artifacts/stt_azure_production_pipeline_20261110.json),
[60 lượt HTTP](artifacts/azure_http_noise_short_20261111.json),
[câu ngắn qua Chrome](artifacts/stt_azure_browser_short_20261111.json),
[nhỏ/quạt qua Chrome](artifacts/stt_azure_browser_quiet_noise_20261111.json),
[đối chiếu PCM](artifacts/stt_azure_browser_integrity_20261111.json),
[đối chiếu provider/API](artifacts/stt_trace_azure_roundtrip_20261111.json).

### So decoder trên cùng file

- FLEURS 12 câu, 351 âm tiết: Whisper 26 lỗi (7,41%), Azure 19 lỗi (5,41%).
- VietSuperSpeech dev: giữ toàn bộ 24 lượt trong artifact; 4 lượt chủ yếu tiếng
  Anh/nhãn lỗi (`vss-05,06,11,15`) tách khỏi phép so tiếng Việt. Trên 20 lượt
  còn lại: Whisper 121/1.064 âm tiết (11,37%), Azure 97/1.064 (9,12%). Nhãn
  VietSuperSpeech là nhãn máy Zipformer, **không phải bản chép human gold**.
- Các giọng tổng hợp từ Azure TTS có thể thuận lợi cho Azure STT; vì vậy phải
  báo riêng giọng người, không gộp thành tỷ lệ độ chính xác ngoài đời.
- Azure không cung cấp revision bất biến của decoder; kết quả dịch vụ có thể
  đổi. Điểm literal tính cả “hai” → “2”, không diễn giải mọi sai khác số chữ
  thành sai ý. Không sửa transcript/nhãn để làm đẹp điểm.

[Whisper trên giọng người](artifacts/stt_natural_whisper_20261110.json),
[Azure trên giọng người](artifacts/stt_natural_azure_20261110.json).

### Model nghiên cứu chưa đưa vào demo

| Ứng viên | Đã chạy | Lý do chưa chọn |
|---|---|---|
| Zipformer 30M int8, greedy/beam8 | Câu mua hàng, quạt, FLEURS, câu ngắn | Có thể sửa một ca thịt tươi nhưng “một lít” → “mục đích”; Có nhỏ → “KHÔNG CÓ”, Dạ → “VÀ”. FLEURS nằm trong training; nhãn VSS cũng do Zipformer sinh; license CC-BY-NC-ND-4.0 |
| Qwen3-ASR 0.6B HF native, CPU float32 | 51 ca, revision ghim, không prompt | 6/6 câu quạt có lời đúng theo thử này nhưng câu ngắn chỉ 3/6; 3/3 không lời sinh chữ khi gọi decoder trực tiếp. FLEURS 32/351 lỗi (9,12%), median khoảng 5,73 giây; còn “lau sàng/nước dạt” |

[Zipformer greedy](artifacts/stt_zipformer_research_greedy_20261111.json),
[beam8](artifacts/stt_zipformer_research_beam8_20261111.json),
[Zipformer câu ngắn](artifacts/stt_zipformer_research_short_20261111.json),
[Qwen3-ASR](artifacts/stt_qwen3_asr_research_20261111.json).

Không đổi model chỉ vì nó sửa được một ví dụ. Mỗi lượt demo vẫn chỉ dùng một
decoder đã chọn trước; không chạy nhiều decoder rồi chọn câu có vẻ hợp mua hàng.

## 3. Lọc ồn nhẹ: có thử đối chiếu, chưa thêm lớp mới

Đã đọc mã DeepFilterNet và thử native 0.5.6 chính thức ở mức giới hạn 6 dB,
không post-filter, không hard gate. Mỗi file có trạng thái model độc lập.
Native `-D` chỉ bỏ delay đầu mà không flush cuối; bộ thử đã thêm 200 ms đuôi
im lặng trước lọc rồi chỉ bỏ phần padding, kiểm tra giữ đúng số mẫu gốc.
Lượt sơ bộ dùng vòng file chung bị dừng; không dùng artifact sơ bộ làm kết luận.

51 file × raw/light = **102 lượt**, 0 lỗi API, 0 câu có lời bị rỗng và 0/3
không lời sinh chữ ở cả hai nhánh. Có/Không/Dạ thường/nhỏ giữ đúng 6/6 ở mỗi
nhánh. Điểm literal 59 → 56 lỗi / 651 âm tiết: câu sạch không đổi, quạt
9 → 8 / 58, FLEURS 19 → 17 / 351. Mức cải thiện nhỏ trên tập đã xem; ca
Hoài My “thịt tươi” có quạt **vẫn sai** ở cả hai nhánh.

Chưa thêm DeepFilterNet vào demo vì chưa có bằng chứng lợi ích đủ ổn định
trên âm mic thật, và nó thêm resampling/độ trễ. Demo giữ browser AEC/NS,
AGC off. Đúng số mẫu không đồng nghĩa âm vị được bảo toàn hoàn toàn.

[Kết quả 102 lượt](artifacts/stt_light_denoise_isolated_20261111.json),
[tổng hợp và quyết định](artifacts/stt_light_denoise_summary_20261111.json).

## 4. Tư vấn: thay đổi code và lỗi còn tồn tại

Luồng hiện tại:

1. LLM hiểu câu mới với lịch sử và nhu cầu trước; tạo kế hoạch tìm loại hàng.
2. Code truy xuất ứng viên, giữ loại, phủ định, quy cách và ngân sách.
3. LLM chọn hàng và nêu lý do bằng kiến thức mua sắm/cách dùng.
4. Code giữ ID/giá/số lượng/khối lượng/tổng tiền, kiểm tra evidence của đúng SKU.
5. **LLM kiểm tra riêng câu đã render**: lựa chọn có phục vụ việc khách cần
   làm không, lý lẽ/cách dùng có hợp dạng hàng không, có tự gán tính năng không.
   Nếu sai thì phản hồi lỗi cụ thể và sửa tối đa một lần, kiểm tra lại trước
   khi trả. Lỗi nhà cung cấp ở bước kiểm tra không được coi là câu đã đạt.

Các sửa bổ sung:

- Giữ ngân sách từ lượt khách bị lỗi provider, không lấy giá assistant làm
  ngân sách. Một câu đổi từ “tầm 100k” sang “dưới 80k” dùng giới hạn sau cùng.
- Không để nhân số gói vượt ngân sách từng món. Tính giá hàng kg theo gram.
- Xác minh quy cách từ chính câu khách: một lít, nửa cân, ba mươi lăm cm.
  Không biến số người ăn hoặc lượng assistant gợi ý thành quy cách bắt buộc.
- Đọc được `2x500ml`, `2×500ml`, dấu nhóm nghìn Việt Nam; không cộng hai chai
  nhỏ thành một chai đúng dung tích. Parser chỉ đọc, không sửa lời STT.
- Không nhầm bông tắm/băng vệ sinh hoặc lấy metadata nhóm hàng làm tên SKU.
- Sửa phạm vi phủ định: giữ “không phải thuốc điều trị mụn”, nhưng chặn câu
  dùng một phủ định không liên quan để lách khẳng định an toàn cho mèo.
- Bỏ cách âm thầm xóa lý do thiếu bằng chứng rồi trả tên/giá. Nay yêu cầu LLM
  sửa lý do; lý do đúng phải hữu ích chứ không chỉ nhắc lại nhãn.
- Sửa lỗi cap 700 ký tự áp nhầm lên toàn bộ câu đã ghép, có thể gây server error
  dù mỗi phần model trả vẫn hợp lệ.

### Bằng chứng chất lượng, không làm đẹp điểm

- Lần audit bản trước: bộ 56 lượt có 20 đạt / 15 một phần / 21 không đạt;
  26 lượt khó có 2 đạt / 7 một phần / 17 không đạt. Đây là baseline, không
  phải điểm của bản có reviewer.
- Sau cấu trúc/evidence ban đầu, 21 lượt HTTP có 2 câu trả được, 19 lỗi quota.
  Hai câu trả được vẫn không đạt hoàn toàn: ép mua ít nhất ba loại thịt;
  hoặc bảo thịt xay “dễ cắt thành lát mỏng”. Đúng SKU/giá chưa đủ.
- Thử NLI mDeBERTa trên 16 ca: không bắt được mâu thuẫn thịt xay/cắt lát,
  đồng thời coi cả lời khuyên nấu ăn/vệ sinh đúng là neutral. Không triển khai
  luật “chỉ entailment mới được nói” vì sẽ quay lại chỉ đọc nhãn.
- Thử reviewer LLM độc lập trên 20 ca: 20/20 khớp đánh giá dự kiến, gồm lỗi
  dạng hàng, quy cách, công dụng và lời khuyên chung đúng. Tập nhỏ đã dùng để
  phát triển prompt, không phải holdout đại diện mọi ngành hàng.
- Test đơn lẻ bằng component reviewer thật: bắt lỗi xay/cắt, chấp nhận thịt
  viên/nấu chín và thịt cắt lát/nhúng lẩu. Một ca dự kiến cho qua nhưng câu
  khách không nhắc mèo bị reviewer từ chối; đã lưu nguyên kết quả, thử riêng
  với đúng ngữ cảnh nhà nuôi mèo thì cho qua. Không sửa artifact để tính 5/5.
- Một lượt đầy đủ `consult()` với planner/composer/reviewer thật đã trả đúng
  một khay ba chỉ bò cắt lát 500g, 182.700đ trong giới hạn 200 nghìn. Câu vẫn
  dài và nhắc lại ngân sách; đánh giá **một phần**, không tuyên bố như nhân viên.
- Bộ 12 lượt HTTP sau reviewer (lẩu, mụn, dầu gội, nước uống) và smoke HTTP
  bị quota chặn. Không có bằng chứng bản sau reviewer đã vượt bộ hội thoại
  toàn diện. Cần chạy lại 35 câu cũ + sửa ý nhiều lượt + câu mới khi đủ hạn mức.

Artifacts:
[audit bản trước](consultation_backend_audit_20261110.md),
[21 lượt HTTP](artifacts/advice_latest_conversations_20261111.json),
[NLI](artifacts/advice_nli_research_20261111.json),
[reviewer 20 ca](artifacts/advice_llm_review_research_20261111.json),
[reviewer từng ca](artifacts/advice_single_review_research_20261111.json),
[ngữ cảnh mèo đúng](artifacts/advice_review_pet_context_20261111.json),
[một lượt đầy đủ](artifacts/advice_semantic_reviewed_probe_20261111.json),
[12 lượt HTTP sau sửa](artifacts/advice_semantic_reviewed_conversations_20261111.json),
[smoke HTTP](artifacts/advice_reviewed_http_smoke_20261111.json).

Giới hạn vận hành: kiểm tra lý lẽ thêm một lượt LLM và có thể thêm lượt sửa;
chưa có đo latency hội thoại đầy đủ sau thay đổi do quota. Reviewer cũng có
thể bỏ sót hoặc từ chối nhầm, không thay thế đọc hội thoại và đánh giá người.
Lỗi quota phải hiện là lỗi dịch vụ, không giả thành “không có trong catalog”.

## 5. Kiểm tra kỹ thuật và phạm vi thực tế

- Backend cuối: **690 passed**; có hồi quy ngân sách/quy cách, provider,
  âm thanh và luồng reviewer từ chối → sửa → kiểm tra lại.
  [JUnit](artifacts/backend_voice_advice_reviewed_20261111.xml).
- Frontend: **47/47**, build đã qua; không sửa frontend sau lần này.
  [Kết quả](artifacts/frontend_voice_advice_20261111.json).
- Backend đã khởi động lại với code reviewer; `/audio/config` xác nhận Azure,
  tiếng Việt, không prompt, Silero v6/YAMNet và catalog cũ.
- Luồng kiosk hiện là HTTP WAV/STT và streaming TTS, không đi qua LiveKit.
  Không gọi các bài test trên là kiểm định phòng LiveKit hay mic/loa vật lý.
- Không thay catalog cũ; không lưu key vào báo cáo; không tắt laptop.

## 6. Nghiên cứu và cách áp dụng

Nguồn được đọc để thiết kế/so thử, không suy từ điểm paper ra kết quả demo:

- [Silero VAD](https://github.com/snakers4/silero-vad) và
  [vad-web FrameProcessor](https://github.com/ricky0123/vad/blob/master/packages/web/src/frame-processor.ts):
  ngưỡng, padding, endpoint; áp dụng bằng phép đo mất phần sửa ý trước/sau.
- [RNNoise](https://github.com/xiph/rnnoise),
  [DeepFilterNet paper](https://arxiv.org/abs/2305.08227),
  [DeepFilterNet implementation](https://github.com/Rikorose/DeepFilterNet):
  thử attenuation limit thay vì hard gate; phát hiện và bù EOF/lookahead ở bộ thử.
- [Zipformer card](https://huggingface.co/hynt/Zipformer-30M-RNNT-6000h),
  [bài VLSP](https://aclanthology.org/2025.vlsp-1.4.pdf): đọc training/license,
  thử CPU, không coi FLEURS/VSS là bằng chứng độc lập cho model này.
- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR),
  [model HF](https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf),
  [paper](https://arxiv.org/html/2601.21337v2): thử native HF ghim revision;
  không triển khai vì kết quả câu ngắn/không lời và độ trễ CPU.
- [ECPO](https://aclanthology.org/2025.findings-acl.307.pdf),
  [CORAL](https://aclanthology.org/2025.naacl-long.392/) và
  [mã CORAL](https://github.com/kookeej/CORAL),
  [llm-convrec](https://github.com/D3Mlab/llm-convrec),
  [NUANCED](https://github.com/facebookresearch/nuanced): tách sở thích/phủ định,
  giữ trạng thái nhiều lượt, đánh giá nhu cầu thay đổi; chưa train ECPO/CORAL.
- [mDeBERTa MNLI/XNLI](https://huggingface.co/MoritzLaurer/mDeBERTa-v3-base-mnli-xnli):
  thử thực rồi loại khỏi kiến trúc triển khai, không lấy score NLI làm chứng minh.
- [Self-Refine paper](https://arxiv.org/abs/2303.17651),
  [mã tham khảo](https://github.com/madaan/self-refine): áp dụng vòng phản hồi
  cụ thể → sửa có giới hạn; vẫn giữ kiểm tra code và test ngoài để tránh tự xác nhận.
- [Groq reasoning](https://console.groq.com/docs/reasoning): xác minh tùy chọn
  model/API khi thử reviewer; không tự chuyển demo sang model enterprise không có quyền.

## 7. Phần chưa hoàn tất

1. Tìm nguyên nhân riêng của “lẩu → lấu” bằng WAV gốc của lượt gặp lỗi. Mẫu
   thử hiện tại không tái hiện được; không thêm bảng thay từ để che lỗi.
2. Đo mic/loa/phòng thật với quạt/điều hòa, lời nhỏ và khoảng nghỉ dài hơn;
   không dùng mic giả làm bằng chứng đã xong phần này.
3. Chạy lại bộ tư vấn sau reviewer với hạn mức đủ, đọc từng câu theo tính
   đúng loại/giữ ý/lý lẽ/hữu ích/tự nhiên, sửa rồi thử lại cả ca cũ và ca mới.
4. Đo độ trễ và tỉ lệ reviewer từ chối nhầm trên hội thoại thật trước khi
   công bố đạt mức nhân viên. Trạng thái kế hoạch vẫn chưa hoàn tất.
