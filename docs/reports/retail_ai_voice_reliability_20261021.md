# Kiểm tra độ ổn định STT/TTS — 21/10/2026

## Đã kiểm tra

- Thu micro bằng Silero VAD trên trình duyệt, chọn đúng micro, cắt câu nói, giữ âm đầu câu và xử lý pause/resume.
- Groq Whisper, metadata chất lượng từng đoạn, lọc transcript có khả năng bịa và sửa từ theo catalog.
- Azure TTS tiếng Việt, cả tổng hợp hoàn chỉnh và truyền âm thanh theo luồng.
- Toàn bộ API `/audio/transcribe` bằng clip bán lẻ tiếng Việt bạn đã gửi.

Đã áp dụng các kỹ thuật phù hợp từ ParrotGo: VAD chạy cục bộ, điều khiển lượt nói tập trung, đệm âm đầu, giới hạn độ dài đoạn, dọn luồng khi hủy phát, TTS streaming và đo thời gian có âm thanh đầu tiên. Codebase vẫn dùng Groq STT và Azure/ElevenLabs TTS hiện có; không đưa nghiệp vụ ParrotGo hay runtime LiveKit vào dự án.

## Lỗi tìm thấy và xử lý

1. **Sửa transcript dài theo catalog quá chậm.** Hàm sửa từ quét mọi taxonomy cho từng khả năng sửa nhãn hàng. Trên transcript mẫu 1.002 ký tự, bước này mất 19.796 ms. Đã lập chỉ mục cụm từ danh mục và gom từ theo độ dài; cùng transcript còn 382 ms. Kết quả không cần sửa từ nào.
2. **Whisper có thể trả một câu trôi chảy dù âm thanh không có lời nói.** Đã yêu cầu và dùng metadata verbose để loại đoạn có no-speech cao, log-probability thấp hoặc compression cao. Vòng lặp cụm nhiều từ giống lỗi đã báo được loại theo quy tắc tổng quát; cách lặp tự nhiên trong yêu cầu như “mua một chai nước, mua thêm hai chai nước” vẫn được giữ. Transcript bị loại không được gửi sang agent bán lẻ.
3. **Sự kiện muộn từ phiên thu âm trước có thể lọt vào phiên mới.** VAD hiện gắn với mã thế hệ lượt thu âm và các thao tác lifecycle được xếp hàng; callback từ micro cũ bị bỏ qua.
4. **STT hiện chưa hiển thị chữ trong lúc người dùng đang nói.** Groq Whisper nhận một đoạn audio hoàn chỉnh sau khi Silero phát hiện hết câu. TTS Azure thì đã stream âm thanh ngay khi có chunk và hủy request upstream khi dừng phát.

## Kết quả chạy thật

| Bài kiểm tra | Kết quả |
| --- | --- |
| Clip bán lẻ 47,69 giây, chạy toàn endpoint `/audio/transcribe` | HTTP 200; 5.253 ms; transcript 1.000 ký tự; `whisper-large-v3`; nội dung đầy đủ, đọc được nhưng vẫn có một số lỗi từ |
| Audio im lặng 2 giây qua `/audio/transcribe` | HTTP 200; `speech_detected=false`; transcript rỗng |
| Sửa transcript theo catalog | 19.796 ms trước tối ưu; 382 ms sau tối ưu |
| Azure TTS tiếng Việt | Tổng hợp và streaming thành công 3/3 lượt |
| Chunk âm thanh TTS đầu tiên | median 178 ms; p95 264 ms |
| Hoàn tất stream TTS | median 250 ms; p95 362 ms |
| Request tổng hợp TTS | median 351 ms; p95 2.224 ms (lượt đầu bị cold start) |

Mẫu TTS chỉ có 3 lượt nên p95 chưa đại diện cho production và còn phụ thuộc mạng. Clip video không có transcript tham chiếu từng chữ, nên không báo WER/CER. Chưa có bản ghi chính xác từ micro của bạn; kết quả clip không chứng minh được độ chính xác với phòng, micro hoặc giọng địa phương của bạn.

## Kiểm tra tự động

- `pytest tests/test_catalog_transcription.py tests/test_groq_services.py`: **45 passed**.
- `pytest tests/test_azure_speech_services.py tests/test_speech_tickets.py tests/test_retail_api.py`: **320 passed**.
- Test kiosk voice/API: **43 passed**.
- Build kiosk production: thành công; Vite còn cảnh báo chunk model 3D lớn.
- Ruff với phần STT/catalog và test liên quan: thành công.

Groq [`verbose_json`](https://console.groq.com/docs/speech-to-text) cung cấp `avg_logprob`, `compression_ratio` và `no_speech_prob` theo từng đoạn; hệ thống dùng chúng để đánh giá đầu vào, không coi câu trả lời trôi chảy là bằng chứng chắc chắn có lời nói. Sửa catalog chỉ thay cụm từ có bằng chứng phù hợp ở gần; tên nhãn hàng không có trong catalog có thể vẫn bị Whisper nhận nhầm.

## Theo dõi lỗi transcript lặp — 21/10/2026

Đối chiếu source ParrotGo tại commit `60760c65011b077acc75ce249f85edf6d28ab243`: các kỹ thuật phù hợp cho kiosk là điều khiển lượt nói tập trung, VAD cục bộ, TTS stream có hủy luồng, đệm một chunk và đo mốc âm thanh đầu tiên. ParrotGo cũng ghi rõ AEC/noise suppression không xác định được người đang nói; tách giọng nền cần bộ lọc chuyên dụng và kiểm tra riêng. Luồng của kiosk đã có các phần phù hợp; Groq Whisper vẫn nhận cả đoạn sau khi người nói ngừng, nên không có partial transcript trong lúc nói.

Phép thử mới với tiếng nền lớn phát hiện lỗ hổng: bộ lọc waveform cũ chỉ chạy khi mức âm thấp. Tín hiệu nền ổn định ở RMS `0.0668` và peak `0.2710` vì thế vẫn bị gửi đến Whisper. Backend nay cũng kiểm tra âm thanh có envelope gần như ổn định; nếu không thấy periodicity/voiced frames, nó loại đoạn trước khi gọi Groq. Kiosk có thêm lớp chặn vòng lặp nhiều từ ngay trước khi gửi câu sang agent, kể cả khi response provider đánh dấu speech là true. Câu lặp tự nhiên trong yêu cầu mua hàng vẫn được giữ.

| Kiểm tra sau sửa | Kết quả |
| --- | --- |
| Groq Whisper, 16 câu tiếng Việt có transcript đối chiếu | Nhận 16/16; 15/16 khớp sau hiệu chỉnh catalog; mean WER `1.56%`, CER `0.57%`; raw Whisper khớp 11/16; median STT `445 ms`, p95 `1,603 ms` |
| Nhiễu phòng lớn, wav mono PCM qua API mới | `speech_detected=false`, transcript rỗng; waveform bị loại trước Groq (RMS `0.0668`, periodicity `0.462`, voiced-frame ratio `0.033`) |
| Transcript vòng lặp giống lỗi người dùng, kể cả khi backend báo speech=true | Không tạo bubble người dùng và không gọi agent; test kiểm tra câu lặp tự nhiên vẫn được giữ |
| Azure TTS trực tiếp, tiếng Việt, 3 lượt | Tổng hợp/stream 3/3; chunk đầu median `204 ms`, p95 `245 ms`; stream xong median `327 ms`, p95 `335 ms` |
| Azure ticket stream qua API mới, 3 lượt | Audio đủ `21,024` byte mỗi lượt; chunk đầu `773 ms` ở lượt đầu lạnh, `168/180 ms` ở hai lượt kế |

Lượt ticket đầu bị cold connection; số liệu chỉ gồm ba lượt và không thay thế đo tại loa. Benchmark 16 câu dùng audio Azure tổng hợp, không phải microphone thật. Chưa có bản ghi mic đúng lúc Whisper tạo transcript lỗi nên không thể xác định chắc đó là room noise, mic ảo/nhầm thiết bị hay lời nói bị cắt. Nếu vẫn gặp, cần lưu đúng đoạn WAV đó để so sánh input waveform, VAD và Whisper output. Không lưu âm thanh hay transcript khách trong log.

## Kiểm tra sau sửa

- `pytest tests/test_groq_services.py tests/test_catalog_transcription.py`: **48 passed**.
- Kiosk voice và chọn micro: **22 passed**, trong đó có loop guard, ca giữ lặp tự nhiên và mặc định mic AMD khi một ID mic ảo cũ được lưu.
- API STT: **1 passed** trong `pytest tests/test_retail_api.py -k transcrib`; endpoint live cũng đã chạy qua noise gate và TTS ticket stream.
- `uv run ruff check backend/services/groq.py tests/test_groq_services.py`: **passed**.
- `npm run build`: **passed**; còn cảnh báo chunk 3D lớn đã có từ trước.
- Bản demo đang mở được trỏ qua `apps/kiosk-airi/.env.local` (file local bị gitignore) sang backend mới tại `http://127.0.0.1:8001`; module Vite đã xác nhận URL này. Backend thử nghiệm 8001 tiếp tục chạy để demo dùng đúng code mới.

## Kiểm tra thêm lỗi micro đầu vào

Khi bật capture trên tab demo hiện tại, UI báo đang dùng `Microphone (DeskIn(R) Virtual Audio Device)`; trong khoảng kiểm tra không có người nói vào mic, meter ở mức `0.000065` trên thang 0–1 và VAD không phát ra câu nói. Đây là kiểm tra thiết bị trong tab, không phải bản ghi giọng nói của người dùng; không có đoạn audio nào được gửi đến Whisper trong lượt này. Vì không có câu nói để thử, phép đo này không kết luận được chất lượng mic hay Whisper; nó chỉ xác nhận phải phân biệt tín hiệu đầu vào với nhận dạng.

Đã đổi chọn mặc định để ưu tiên `Microphone Array (AMD Audio Device)` khi thiết bị này có trong danh sách, thay vì giữ một ID cũ của micro ảo. Lựa chọn người dùng chủ động trong picker vẫn được lưu và tôn trọng; micro ảo chỉ được dùng khi người dùng chọn rõ hoặc không có micro ưu tiên khác. Khi mức tín hiệu gần bằng 0 quá 8 giây, giao diện chỉ rõ micro đang chọn hầu như không có tín hiệu để người dùng biết cần đổi input.

Xác nhận bằng 22 unit tests frontend, 48 unit tests backend STT/catalog và build production. Trong môi trường tab thử hiện tại chỉ có micro ảo DeskIn, không có AMD để kiểm chứng capture thật trên thiết bị vật lý. Cần đo một câu nói thu từ AMD trên máy đang chạy demo để kết luận về WER của microphone thực.

## Đối chiếu model và luồng demo — 21/10/2026

Chạy lại 16 câu tiếng Việt do Azure tạo, mỗi câu có transcript chuẩn đầy đủ. Với `whisper-large-v3`, pipeline chấp nhận 16/16; hiệu chỉnh theo catalog đạt khớp chính xác 15/16, WER trung bình 1.56%, CER 0.57%, STT median/p95 448/1,521 ms. Whisper thô khớp 11/16. Lỗi còn lại là tên Sunsilk chưa có trong catalog nên được giữ nguyên theo âm thanh là “sung sinh”.

Chạy cùng 16 câu với `whisper-large-v3-turbo`: khớp chính xác 9/16 sau hiệu chỉnh, WER 7.25%, CER 3.13%, STT median/p95 371/1,387 ms. Turbo nhanh hơn 77 ms ở median nhưng kém sáu câu chính xác; giữ `whisper-large-v3` làm model chính và turbo làm dự phòng phù hợp với bộ đo này. Đây là câu Azure tổng hợp, không phải đo giọng người hoặc phương ngữ.

Đo TTS Azure trực tiếp trong ba lượt: tổng hợp median/p95 227/772 ms; chunk đầu median/p95 140/143 ms; stream hoàn tất median/p95 222/246 ms. Đo đường ticket HTTP của kiosk trong ba phiên mới: chunk đầu 610–778 ms (median 763 ms), nhận đủ audio 880–1,077 ms (median 997 ms). Chênh lệch gồm lượt yêu cầu ticket, kết nối HTTP tới kiosk và kết nối Azure lạnh; đây là mốc sau khi đã có câu trả lời, chưa tính thời gian LLM.

Gọi endpoint `/audio/transcribe` live với WAV im lặng 2 giây và tone 440 Hz 2 giây: cả hai trả HTTP 200, `speech_detected=false`, text rỗng; lượt mất 17 ms và 95 ms. Test giao diện xác nhận mẫu transcript lặp bị chặn trước tin nhắn và agent ở cả callback audio Whisper lẫn callback transcript phụ. Kết quả này kiểm tra đường chặn đầu vào; nó không cho biết nguyên nhân của âm thanh mic thật nếu chưa có chính WAV bị lỗi.

Các kết quả chạy lại được lưu ở `evaluation/results/live_groq_stt_large_rerun_20261021.json`, `evaluation/results/live_groq_stt_turbo_rerun_20261021.json` và `evaluation/results/live_azure_tts_stream_rerun_20261021.json`.

Kiểm tra sau thay đổi: backend **372 passed**, frontend **57 passed**, `npm run build` thành công và Ruff với helper định tuyến/test liên quan thành công. Build còn cảnh báo chunk model 3D vượt 500 kB.

Chưa có bản WAV thu đúng lúc bạn nói câu bị nhận sai. Vì vậy chưa thể tính WER cho câu đó hoặc kết luận nó đến từ giọng bị cắt, tiếng vọng, micro ảo hay Whisper. Đường lặp sai đã có guard chung ở giao diện; demo đang chạy trên `http://127.0.0.1:5173/` để thử lại với phiên mới.

## Kiểm tra lại đúng tab demo — 21/10/2026

Mở micro từ tab demo `http://127.0.0.1:5173/` và kiểm tra danh sách thiết bị sau khi cấp quyền: tab chỉ thấy `Microphone (DeskIn(R) Virtual Audio Device)`; không thấy `Microphone Array (AMD Audio Device)` mà ứng dụng ưu tiên. Meter đứng quanh `0.000064–0.000070` trên thang 0–1. Sau 10 giây, cảnh báo “Micro đang chọn gần như không có tín hiệu” xuất hiện đúng; không có câu thoại hay request STT nào được tạo trong lần kiểm tra này. Đã dừng capture và để demo ở trạng thái sẵn sàng.

Điều này xác nhận tab demo hiện tại đang thu từ đầu vào ảo gần như im lặng. Nó có thể giải thích vì sao Whisper không nhận đúng khi thử ngay trong tab này; chưa thể quy lỗi cho model hay đánh giá giọng nói thật khi AMD không được trình duyệt cung cấp. Cần mở demo trong trình duyệt có `Microphone Array (AMD Audio Device)` hoặc đổi đầu vào hệ điều hành rồi kiểm tra meter trước khi đánh giá STT.

Đã chạy lại hai model trên 32 giây đầu của audio M4A tiếng Việt người dùng gửi: `whisper-large-v3` mất `2,977 ms`, `whisper-large-v3-turbo` mất `1,469 ms`; cả hai cho transcript gần như giống nhau. Văn bản người dùng cung cấp có mốc thời gian nhưng không phải bản chép nguyên văn đầy đủ đã đối chiếu với audio, vì vậy JSON chỉ lưu độ trễ và transcript, cố ý bỏ WER/CER. Kết quả tại `evaluation/results/live_groq_stt_user_clip_excerpt_20261021.json` không đại diện cho micro trực tiếp.

Thêm cảnh báo nhận diện đầu vào ảo: khi luồng mở bằng thiết bị có tên `Virtual`, `DeskIn`, `Cable Input`, `Stereo Mix`, `Loopback` hoặc `VoiceMeeter`, kiosk báo ngay để khách kiểm tra thanh tín hiệu và chọn micro thật. Xác nhận trên tab demo: cảnh báo xuất hiện khi đang thu từ DeskIn; meter vẫn gần im lặng; sau kiểm tra đã dừng micro và để demo sẵn sàng.

Kiểm tra sau thay đổi: backend voice/API **368 passed**; frontend **58 passed**; build production thành công; Ruff thành công. Build vẫn hiện cảnh báo chunk model 3D khoảng 1 MB.

## Chạy lại theo phản ánh câu nhận dạng sai — 21/10/2026

Đối chiếu source ParrotGo ở commit `60760c65011b077acc75ce249f85edf6d28ab243`: giữ VAD cục bộ và điều khiển vòng đời thu/phát; TTS stream có thể hủy; đo chunk âm thanh đầu tiên. Kiosk đã có các phần này. Groq Whisper nhận đoạn hoàn chỉnh sau VAD, không có transcript từng phần trong lúc nói; Azure Speech vẫn stream TTS.

Tìm thấy lỗi thực trong gợi ý catalog cho STT: bộ làm sạch prompt xóa dấu `/`, biến nhãn `P/S` thành `P S`. Đã giữ dấu `/` trong danh sách từ khóa và hướng dẫn Whisper giữ đúng cách viết/viết tắt của tên trong catalog. Bộ hiệu chỉnh viết tắt chỉ sửa khi khớp chính xác loại hàng và có một ứng viên catalog gần nhất; khi ứng viên bằng điểm thì giữ nguyên transcript.

| Bài kiểm tra sau sửa | Kết quả |
| --- | --- |
| Groq `whisper-large-v3`, 16 câu bán lẻ tổng hợp Azure có transcript chuẩn | 16/16 được nhận; 15/16 khớp chính xác sau hiệu chỉnh catalog; WER 1,56%, CER 0,57%; raw Whisper khớp 11/16; STT median/p95 456/1.565 ms |
| Hỏi kem đánh răng P/S | Transcript khớp chính xác sau khi prompt giữ `P/S` thay vì tách dấu; không cần sửa bằng đoán ngoài catalog |
| Hỏi dầu gội Sunsilk đang khuyến mãi | Whisper trả “sung sinh”; catalog runtime không có Sunsilk nên hệ thống giữ nguyên, không tự biến thành thương hiệu khác |
| Azure TTS tiếng Việt, synthesis và stream | 5/5 thành công; chunk đầu median/p95 196/657 ms; stream hoàn tất median/p95 329/1.573 ms |
| Câu lỗi “Cảm ơn các bạn đã theo dõi…” có cụm lặp | Guard backend và frontend chặn trước tin nhắn/agent; test riêng câu này tiếp tục đạt |

Đo TTS p95 bị ảnh hưởng bởi lượt chậm/cold start; năm lượt chưa đủ để đại diện production. STT 16 câu dùng giọng Azure tổng hợp, không thay cho WER trên micro thật. Tab demo hiện chỉ liệt kê `Microphone (DeskIn(R) Virtual Audio Device)`, không thấy `Microphone Array (AMD Audio Device)`; vì thế chưa thu được chính đoạn micro gây câu nhận dạng sai. Cần test lại bằng thiết bị thu giọng thật để xác định lỗi còn lại nằm ở micro/âm lượng/phòng hay model.

Kiểm tra code sau sửa: backend STT/catalog/API/TTS **370 passed**; frontend voice/transcript/microphone **27 passed**; production build thành công; Ruff thành công. Build còn cảnh báo chunk model 3D khoảng 1 MB. Kết quả benchmark mới lưu tại `evaluation/results/live_groq_stt_catalog_benchmark_20261021_promptfix.json` và `evaluation/results/live_azure_tts_benchmark_20261021_parrotgo_recheck.json`.

## Tái hiện API sau phản ánh mới — 21/10/2026

Chạy câu “Có kem đánh răng P/S cho răng nhạy cảm không?” qua đúng endpoint `/audio/transcribe` bằng audio Azure tiếng Việt. Whisper `whisper-large-v3` tách tên thành “P, S”; lớp hiệu chỉnh đọc đúng nhóm Kem đánh răng và chuẩn hóa thành “P/S”. Lượt cuối qua API trả đúng P/S, rồi agent gọi `search_products` và đưa các mặt hàng P/S thực có trong catalog. Catalog không xác nhận công dụng cho răng nhạy cảm nên agent không khẳng định sản phẩm nào phù hợp.

Lỗi gốc của biến thể “P, ép” là bộ lọc từ dừng loại nhầm token `ép` trước khi hiệu chỉnh. Đã bổ sung đối chiếu phát âm cho thương hiệu viết tắt hai ký tự: chỉ xét khi nhóm hàng khớp chính xác, chỉ sửa khi có một ứng viên tốt nhất rõ ràng, và chuẩn hóa cụm đã khớp về cách viết trong catalog. Với catalog hiện tại, `P, ép`, `P, F`, `P, S` thành `P/S`; `P, T` được giữ nguyên. Cách làm dựa trên từ điển catalog và tên chữ cái tiếng Việt, không gắn riêng với một câu hỏi.

| Kiểm tra chạy thật | Kết quả |
| --- | --- |
| WAV im lặng 2 giây qua `/audio/transcribe` | HTTP 200; `speech_detected=false`; transcript rỗng; 19 ms; không gọi agent |
| Azure audio → Groq STT → catalog agent | Transcript đúng P/S; model STT `whisper-large-v3`; STT 2.779 ms; agent dùng `search_products`, trả sản phẩm catalog trong 169 ms |
| TTS ticket stream sau agent | Đủ 125.136 byte MP3; lượt end-to-end đầu có chunk đầu ở 3.700 ms khi kết nối lạnh/chậm |
| Cùng câu trả lời TTS qua 3 ticket stream tiếp theo | Chunk đầu `498/183/151 ms`; audio hoàn tất `1.005/620/591 ms`; đủ byte cả 3 lượt; median chunk đầu `183 ms`, median hoàn tất `620 ms` |

Kiểm tra tự động sau thay đổi: backend STT/catalog **50 passed** và bộ backend voice/API/catalog/TTS **370 passed**; Ruff thành công. Test vẫn giữ nguyên yêu cầu không tự sửa cụm viết tắt mơ hồ. Guard backend và frontend chặn transcript outro lặp đã báo trước khi nó tạo tin nhắn hoặc gọi agent.

Giới hạn còn lại: tab demo chỉ có micro ảo DeskIn, meter gần 0; không có `Microphone Array (AMD Audio Device)`. Chưa thu được WAV thật tạo ra câu outro sai, nên audio Azure tổng hợp không thể thay cho WER trên giọng người hoặc chứng minh nguyên nhân riêng của lần đó. Groq Whisper vẫn nhận audio theo cả đoạn sau VAD, không hiển thị transcript từng từ khi người dùng đang nói. TTS có stream audio, nhưng một request lạnh vẫn có thể chậm hơn đáng kể; cần đo trên micro/loa và mạng thực tế để có p95 đáng tin.

## Tái kiểm tra độ chính xác STT — 21/10/2026

Mở rộng gợi ý Whisper từ câu hỏi mua hàng sang hội thoại cửa hàng, gồm hỏi hàng/giá/khuyến mãi/công dụng/vị trí/tồn kho và tư vấn mặt bằng, nhập hàng, nhu cầu, vốn, dòng tiền. Prompt giữ dấu, tên riêng, viết tắt, khẩu ngữ vùng miền và chỉ chép lời nghe được. Thêm chặn byte UTF-8 880 cho prompt có từ catalog. Lý do: một prompt thử dài 1.073 byte bị Groq trả HTTP 400 (giới hạn báo trong lỗi là 896); prompt catalog hiện tại trước sửa đã sát ngưỡng ở 895 byte.

| Bài kiểm tra | Kết quả |
| --- | --- |
| Google FLEURS `vi_vn/test`, 12 audio đầu, chạy qua `GroqService` với prompt mới | 12/12 được nhận; `whisper-large-v3`; 4/12 khớp nguyên văn; mean WER 7,42%, CER 4,04%; median 818 ms, p95 2.886 ms |
| So với prompt cũ trên cùng 12 câu | WER 7,52%, CER 4,33%, khớp nguyên văn 4/12; prompt mới chỉ cải thiện nhẹ (0,10 điểm WER), chưa đủ mẫu để kết luận khác biệt có ý nghĩa |
| Clip M4A người dùng gửi qua đúng endpoint `/audio/transcribe` | HTTP 200; `speech_detected=true`; 6.260 ms; nhận đúng “dòng tiền” sau khi đổi ngữ cảnh prompt, nhưng vẫn nhầm cụm “đồ ăn vặt … ngoài cổng trường” thành “món quà vật … cộng trường” |
| Prompt catalog dài 32 thuật ngữ | 872 byte; không vượt ngưỡng; test đầu vào dài nhiều dấu tiếng Việt vẫn bị cắt danh sách thuật ngữ an toàn, không cắt giữa prompt hoặc trả 400 |

FLEURS là lời đọc theo văn bản, không đại diện hội thoại tự nhiên. 12 mẫu được lấy đầu archive và đều là giọng nam theo nhãn bộ dữ liệu; kết quả này không đo phương ngữ nữ/Trung/Nam hoặc microphone thật. Clip M4A là giọng người nói tự nhiên nhưng không có transcript nguyên văn đã xác minh, vì vậy không báo WER/CER. Benchmark JSON: `evaluation/results/live_groq_stt_fleurs_largev3_promptguard_20261021.json`, `evaluation/results/live_groq_stt_user_clip_endpoint_20261021.json`.

Kiểm tra sau sửa: STT/catalog/backend API **59 passed**, kiosk VAD/microphone/transcript **17 passed**, Azure TTS/stream-ticket **14 passed**, Ruff **passed**. Đây chưa phải xác nhận STT trên microphone thật: lần kiểm tra demo gần nhất chỉ thấy `DeskIn(R) Virtual Audio Device` với meter gần im lặng, không thấy `Microphone Array (AMD Audio Device)`.

Thử thêm biến thể prompt “quà vặt, ngoài cổng trường” (877 byte) trên cùng clip để kiểm tra lỗi cụm từ: thời gian 3.382 ms nhưng Whisper vẫn nhầm hai cụm này. Không thêm luật sửa riêng theo câu; kết quả cho thấy prompt một mình chưa đủ để khắc phục đoạn âm thanh đó.

## Kiểm tra audio hội thoại công khai và noise gate — 21/10/2026

Để không cần ghi âm từ micro của người dùng, tải 24 đoạn thoại tiếng Việt ngắn từ [VietSuperSpeech](https://huggingface.co/datasets/thanhnew2001/VietSuperSpeech), mỗi video một đoạn, rồi benchmark cả hai model qua `GroqService` với cùng prompt tiếng Việt. Bộ transcript được tạo tự động bằng Zipformer, không phải nhãn đã được người chép kiểm tra; WER/CER dưới đây chỉ đo độ khớp với pseudo-reference.

| Model | Nhận được | WER / CER pseudo-reference | Median / p95 |
| --- | ---: | ---: | ---: |
| `whisper-large-v3` | 24/24 | 14,93% / 11,76% | 972 / 2.100 ms |
| `whisper-large-v3-turbo` | 24/24 | 14,64% / 11,26% | 848 / 1.541 ms |

Mỗi lượt benchmark có ba lần rate limit ở key chính; retry qua key kế tiếp thành công và không có clip thất bại. Chênh lệch hai model trên bộ nhãn tự động quá nhỏ để đổi model chính. Trên 12 câu FLEURS có transcript người chép, `large-v3` đạt WER 7,42%, `turbo` 7,77%; lượt turbo chạy trong phiên có độ trễ cao do điều kiện rate-limit không ngang bằng. Trên 16 câu retail có transcript chuẩn, large-v3 sau hiệu chỉnh catalog đúng 15/16, turbo đúng 9/16. Giữ `large-v3` làm chính và `turbo` làm model dự phòng khi provider lỗi.

Có một đoạn mà large-v3 trả câu ngắn, trôi chảy nhưng WER 100% theo nhãn dữ liệu; turbo nhận dạng đoạn đó sát nhãn hơn. Tuy nhiên vì nhãn VietSuperSpeech cũng do model sinh, chưa thể khẳng định transcript nào đúng với âm thanh. Các trường `avg_logprob`, `no_speech_prob` và compression giúp phát hiện một số audio yếu nhưng không chứng minh transcript đúng ngữ nghĩa. Không nên tuyên bố hệ thống hết hallucination; câu hỏi về hàng hóa vẫn phải được đối chiếu với catalog, và audio không rõ cần được hỏi lại.

Gửi qua API đang chạy `POST /audio/transcribe`: WAV im lặng, nhiễu đều mức thấp, tone 440 Hz và một click 10 ms đều trả `speech_detected=false`, text rỗng trong lần lượt 40,6 / 146,2 / 129,9 / 30 ms, không gọi Whisper. Một đoạn thoại công khai 14,8 giây được API nhận trong 1.922 ms. Bộ lọc click mới xét tín hiệu thưa theo frame và mức hỗ trợ giọng nói; không gắn với transcript hay câu nói cụ thể. Bài kiểm tra này dùng file audio và tín hiệu tổng hợp, không thu micro vật lý. Tab trước đó chỉ thấy đầu vào ảo DeskIn; thiết bị AMD chưa được browser cung cấp.

Kiểm tra sau thay đổi: `tests/test_groq_services.py` **42 passed**; lựa chọn pytest STT/API **31 passed**; test frontend mic/VAD/transcript **30 passed**; Ruff và `git diff --check` đều qua. JSON: `evaluation/results/live_groq_stt_vietsuperspeech_vi_filtered_20261021.json`, `evaluation/results/live_groq_stt_fleurs_turbo_recheck_20261021.json`, `evaluation/results/live_groq_stt_api_noiseguard_20261021.json`.

## Benchmark audio công khai qua demo đang chạy — 21/10/2026

Để kiểm tra mà không cần người dùng gửi bản thu mic, lấy 19 câu ngẫu nhiên có transcript tham chiếu từ tập test VIVOS, mỗi người nói một câu, rồi gửi audio vào chính endpoint `POST /audio/transcribe` trên demo đang chạy ở cổng 8001. VIVOS có 19 người nói và 760 câu test; bản ghi được đọc trong môi trường yên tĩnh bằng micro chất lượng cao, nên đây là bài kiểm tra lời đọc sạch, không phải hội thoại tự nhiên hay âm thanh từ mic của kiosk. Audio chỉ được tải trong lúc chạy; file kết quả không chứa audio hoặc URL tải có chữ ký.

| Đo trên endpoint đang chạy | Kết quả |
| --- | ---: |
| Audio có lời được nhận | 19/19 |
| Khớp transcript nguyên văn | 5/19 |
| WER gộp / CER gộp | 10,23% / 4,76% |
| Độ trễ toàn endpoint median / p95 | 578 / 1.239 ms |
| Model STT được dùng | `whisper-large-v3` |

19/19 là tỷ lệ nhận ra đoạn có lời, không có nghĩa là chép đúng hết: WER 10,23% cho thấy vẫn còn lỗi từ ngay cả với lời đọc sạch. Độ trễ bắt đầu khi gửi đoạn audio đã thu xong, gồm upload và xử lý endpoint; chưa gồm người dùng nói, chờ VAD chốt câu (hiện 1,4 giây), gọi agent hoặc phát TTS. Bộ test này không đại diện giọng địa phương, phòng ồn hay micro vật lý của người dùng. Nguồn VIVOS ghi rõ có 19 người nói/760 câu test, thu trong môi trường yên tĩnh và người nói đọc từng câu; giấy phép chỉ dành cho mục đích học thuật [VIVOS trên Zenodo](https://zenodo.org/records/7068130).

Đo lại TTS qua ticket-stream HTTP đang chạy, sau khi câu trả lời đã có: Azure `vi-VN-HoaiMyNeural` thành công 8/8; chunk âm thanh đầu median/p95 743/883 ms; nhận đủ stream median/p95 1.066/1.366 ms. Đây là độ trễ API đến byte audio, không đo thời gian loa phát hoặc toàn bộ thời gian từ lúc bắt đầu nói. File: `evaluation/results/live_azure_tts_ticket_stream_20261021_final.json`.

Gửi 6 dạng audio tổng hợp qua endpoint — im lặng, tone 440 Hz, click đơn, nhiễu trắng đều, nhiễu trắng điều biên và nhiễu ngắt quãng — cả 6 đều trả transcript rỗng với `speech_detected=false`. Phát lại chính câu outro bị lặp qua Azure TTS rồi đưa lại endpoint STT: Whisper vẫn báo metadata như audio có lời, nhưng guard lặp chặn kết quả trước khi tạo transcript cho người dùng hoặc gọi agent. Các bài này kiểm tra tín hiệu tổng hợp và câu lặp đã biết; chúng không chứng minh nguyên nhân của bản thu micro lỗi thật. Kết quả: `evaluation/results/live_stt_noise_gate_recheck_20261021.json` và `evaluation/results/live_stt_repeated_phrase_roundtrip_20261021.json`.

Runner có thể chạy lại ở `evaluation/live_groq_stt_vivos_benchmark.py`; kiểm thử chọn mẫu kiểm tra mỗi speaker chỉ có một audio, lựa chọn có seed ổn định, bỏ câu quá ngắn/thiếu audio, và nhận cả hai dạng audio metadata. Sau bổ sung, nhóm test backend STT/catalog/Groq/Azure TTS/ticket đạt **77 passed**, test frontend VAD/transcript/microphone đạt **17 passed**, Ruff đạt.

Đối chiếu ParrotGo tại commit local `60760c65011b077acc75ce249f85edf6d28ab243`: kiosk hiện đã có VAD cục bộ, đệm đầu câu, TTS streaming, làm ấm kết nối và đo byte audio đầu; vẫn khác ở chốt câu: kiosk đợi 1,4 giây còn cấu hình ParrotGo khoảng 0,55 giây, nên kiosk có thể chậm thêm khoảng 0,85 giây sau khi ngừng nói. Chưa rút ngắn ngưỡng vì VIVOS là lời đọc tách câu, không cho biết pause tự nhiên trong hội thoại tiếng Việt sẽ bị cắt hay không. Kiosk cũng dùng Groq Whisper theo từng đoạn hoàn chỉnh, nên không hiện transcript từng phần khi người dùng đang nói.

Giới hạn đang mở: chưa có WAV thu từ micro vật lý lúc lỗi xuất hiện. Tab browser trước đó chỉ cung cấp mic ảo DeskIn với meter gần im lặng; benchmark công khai không thay thế được đầu vào này. Vì vậy đã xử lý được mẫu outro lặp đã biết và đo luồng API/TTS, nhưng chưa thể xác nhận lỗi thu âm trên micro AMD hoặc độ chính xác với giọng địa phương cụ thể.

## Tự tìm audio và kiểm tra mic trong tab demo — 21/10/2026

Tải lại đúng bộ 19 audio VIVOS có transcript tham chiếu và gửi qua endpoint demo `/audio/transcribe`; không lưu audio hay URL ký trong kết quả. Đây là lượt lặp lại cùng seed để xác nhận trạng thái hiện tại, không phải 19 mẫu mới độc lập.

| Đo trên endpoint | Kết quả |
| --- | ---: |
| Có lời được nhận | 19/19 |
| Transcript khớp nguyên văn | 5/19 |
| WER gộp / CER gộp | 10,23% / 4,76% |
| Median / p95 endpoint | 1.338 / 2.476 ms |
| Model | `whisper-large-v3` |

Transcript và độ trễ được lưu tại [live_groq_stt_vivos_user_followup_20261021.json](../../evaluation/results/live_groq_stt_vivos_user_followup_20261021.json). Kết quả WER lặp lại khớp với lần trước; độ trễ lần này cao hơn và biến thiên lớn hơn, nên dùng median/p95 của từng lượt thay vì coi chúng là hằng số. Đây vẫn là lời đọc sạch, không đại diện hội thoại tự nhiên hay giọng địa phương.

Trong tab demo, trình duyệt hiện liệt kê và chọn được `Default - Microphone Array (AMD Audio Device)` cùng mic ảo DeskIn. Mình phát một câu WAV VIVOS có transcript “Hầu hết những vấn đề xảy ra trên thế giới” trong tab kiểm thử; mức mic hiển thị `0.000168`, app cảnh báo “Micro đang chọn gần như không có tín hiệu”, không tạo transcript. Phép thử xác nhận đường phát trong tab đó không tới được mic đang chọn; nó **không chứng minh mic AMD hỏng**, vì playback trình duyệt có thể bị chặn/khử hồi âm và không thay thế lời nói trực tiếp. Mic đã được dừng và bản sao WAV/HTML tạm đã xóa.

Không thể tính WER cho mic AMD nếu không có lời nói thật đi vào đầu vào đó. Vì vậy kết quả có thể khẳng định là endpoint STT xử lý được audio sạch với WER 10,23%; nguyên nhân câu outro lặp trước đây đã có guard chặn; còn lỗi thu lời nói thật trên mic hiện tại vẫn chưa được xác minh bằng giọng nói qua chính mic.

## Xác nhận code sau lượt tự tìm audio — 21/10/2026

Benchmark VIVOS qua endpoint hiện tại hoàn tất 19/19 clip, 5/19 khớp nguyên văn, WER gộp 10,23%, CER gộp 4,76%, latency median/p95 1.338/2.476 ms trên `whisper-large-v3`. Đây là cùng bộ seed 19 speaker đã dùng trước đó, chạy lại để xác nhận; không phải 19 mẫu mới. Kết quả chi tiết: [live_groq_stt_vivos_user_followup_20261021.json](../../evaluation/results/live_groq_stt_vivos_user_followup_20261021.json).

Sau các thay đổi trong working tree, backend toàn bộ **878 passed**, frontend **62 passed**, build production thành công, Ruff và `git diff --check` đều đạt. Frontend local và backend `/health` đang trả HTTP 200. Audio thử nghiệm không đi được qua đường phát lại trình duyệt tới mic AMD, meter gần im lặng; vì vậy chưa có WER cho mic thật và chưa thể khẳng định thiết bị hay điều kiện thu âm đã được xử lý dứt điểm. VIVOS cũng không đại diện giọng địa phương hoặc hội thoại tự nhiên.

## Tái hiện transcript giả từ mic yên lặng và chặn trước upload — 21/10/2026

Mở lại demo, chọn đúng `Default - Microphone Array (AMD Audio Device)`, để yên khoảng 8 giây rồi bấm dừng. Meter ở `0.000274`; dù không có câu nào được nói chủ động trong lượt thử, app vẫn gửi transcript “Tiếng tiền thì đìa hoa cho Thực cơ. Việt!” vào chat và agent trả lời như thể khách đã hỏi. Đây là tái hiện được lỗi mic/VAD tạo transcript giả; mức tín hiệu thấp không đủ để kết luận thiết bị hỏng hay xác định có âm nền nào lọt vào đoạn thu.

Đã thêm energy gate ở `VoiceConversationController.finishSegment`: chỉ gửi đoạn lên `/audio/transcribe` khi có ít nhất 160 ms gồm các frame 512 mẫu có RMS từ `0.00075` trở lên. Ngưỡng này bám theo mức meter “gần như không có tín hiệu” sẵn có; giọng dưới ngưỡng được hỏi nói gần mic/chọn mic khác thay vì bị Whisper biến thành câu. Nếu một segment gần im lặng bị VAD phát hiện trong chế độ nghe rảnh tay, controller mở lại mic; thao tác dừng thủ công vẫn để mic dừng.

Tải lại demo và lặp lại đúng phép thử idle trên mic AMD: sau 8 giây, meter `0.000221`; bấm dừng không tạo tin nhắn người dùng hay câu trả lời của agent, trạng thái trở về `Bắt đầu nói`. Không chủ động nói trong lượt thử nên đây xác nhận nhánh im lặng được chặn, không phải độ chính xác trên giọng thật. Test frontend sau bổ sung: **64 passed**; backend đầy đủ: **879 passed**; production build và Ruff thành công. Thêm unit test xác nhận segment 500 ms ở mức giọng nhỏ ngay trên sàn vẫn được gửi đi.

Kết quả STT công khai hiện có vẫn là VIVOS 19 người nói: 19/19 được nhận, WER 10,23%, CER 4,76%, median/p95 1.338/2.476 ms trên endpoint demo với `whisper-large-v3`. Đây là lời đọc sạch; chưa có WAV giọng địa phương thu từ mic AMD để tính WER. TTS đã đo riêng 8/8 stream thành công; median/p95 tới byte âm thanh cuối là 1.066/1.366 ms, không gồm thời gian loa phát.

## Chạy lại audio người dùng và endpoint demo — 21/10/2026

Tìm lại file M4A 47,69 giây người dùng đã gửi và chạy qua `GroqService`, sau đó chạy lần nữa qua đúng endpoint của demo `POST /audio/transcribe` với session mới. Cả hai lượt đều được nhận bằng `whisper-large-v3`; thời gian lần lượt là **3.904 ms** và **2.876 ms**. Lượt endpoint trả HTTP 200, `speech_detected=true`, `no_speech_prob=0,0002`, `avg_logprob=-0,087`, `compression_ratio=1,63`; transcript là một đoạn liên tục, không có vòng lặp outro. Kết quả endpoint: [live_groq_stt_user_audio_endpoint_recheck_20261021.json](../../evaluation/results/live_groq_stt_user_audio_endpoint_recheck_20261021.json).

Không chấm WER/CER cho file này: phần chữ kèm theo là caption/tóm tắt theo mốc thời gian, không phải transcript nguyên văn đã kiểm tra. Các WER/CER khoảng 1,01/0,90 trong benchmark cũ `live_groq_stt_voice_problem_baseline_20261021.json` vì thế không đo chính xác chất lượng STT và không nên dùng để kết luận model nhận sai toàn bộ. Bản chạy lại chỉ xác nhận đường upload/session/catalog và chất lượng metadata; không thay thế đánh giá transcript nguyên văn.

Chuỗi outro lặp người dùng đã nêu được kiểm tra ở cả lớp Whisper backend và trước khi frontend gửi câu sang retail agent. Bộ test này xác nhận transcript lặp bị chặn; test không cho thấy câu đó còn có thể đi vào agent qua đường xử lý hiện tại. Mức kiểm tra sau cùng: **891 test backend**, **32 test frontend tập trung vào voice/mic/transcript**, **56 test riêng STT/TTS backend**, production build thành công, Ruff và `git diff --check` đạt. Frontend `5173` và backend `/health` đều trả HTTP 200. Build vẫn hiện cảnh báo chunk `model-viewer` khoảng 1 MB; không ảnh hưởng kiểm tra STT.

Giữ `whisper-large-v3` làm model chính: trên bộ retail có transcript chuẩn nó đạt 15/16 khớp sau hiệu chỉnh catalog, so với 9/16 của turbo; trên 19 câu VIVOS WER là 10,23%. Turbo vẫn là fallback khi provider/request lỗi. Groq hiện cũng khuyến nghị `large-v3` cho trường hợp nhạy lỗi và `large-v3-turbo` khi ưu tiên hiệu năng; đây là thống kê chung của Groq, không phải bảo đảm riêng cho tiếng Việt ([Groq Speech-to-Text](https://console.groq.com/docs/speech-to-text)).

Giới hạn còn lại: file được chạy lại là audio có sẵn, chưa phải giọng nói thu qua micro AMD trên máy. Benchmark VIVOS/FLEURS và M4A xác nhận endpoint hoạt động; không đủ dữ liệu để tính WER hoặc kết luận chất lượng thu âm của thiết bị trong hội thoại trực tiếp.

## Tự tìm audio công khai và chạy lại toàn endpoint — 21/10/2026

Lấy 3 WAV tiếng Việt có transcript tham chiếu từ tập test `google/fleurs` (CC BY 4.0), tạo session mới và gửi từng file qua endpoint demo đang chạy `POST /audio/transcribe`. Cả 3 được nhận bằng `whisper-large-v3`, không dùng fallback; 2/3 khớp nguyên văn, mean WER 1,92%, CER 1,17%, median endpoint 2.093 ms. Kết quả chỉ là smoke test 3 clip đọc sạch, không thay cho benchmark ngẫu nhiên lớn hoặc âm thanh mic thật.

| Clip | Thời lượng | WER | Độ trễ |
| --- | ---: | ---: | ---: |
| `10057024225045006889.wav` | 11,34 giây | 0% | 1.954 ms |
| `1015279164912843543.wav` | 14,88 giây | 0% | 2.806 ms |
| `10169179696365895477.wav` | 22,56 giây | 5,8% | 2.093 ms |

Audio mẫu để tải và thử trực tiếp: [10057024225045006889.wav](../../.tmp/fleurs_vi_test_samples/10057024225045006889.wav). Nguồn và giấy phép: [Google FLEURS trên Hugging Face](https://huggingface.co/datasets/google/fleurs). Báo cáo chi tiết: [live_groq_stt_fleurs_endpoint_followup_20261021.json](../../evaluation/results/live_groq_stt_fleurs_endpoint_followup_20261021.json).

## Đo lại FLEURS qua toàn endpoint và kiểm tra thiết bị đầu vào — 21/10/2026

Chạy lại đủ 12 WAV tiếng Việt đã tải từ `google/fleurs` qua session demo và `POST /audio/transcribe` (không gọi service STT trực tiếp). Cả **12/12** được nhận với `whisper-large-v3`; **4/12** khớp nguyên văn. WER gộp **8,83%**, CER gộp **5,01%**; WER/CER trung bình theo câu **8,89% / 4,80%**. Độ trễ endpoint median **834 ms**, p95 **3.130 ms**. Đây là lời đọc sạch; FLEURS không kiểm tra giọng địa phương, hội thoại tự nhiên hay âm học của mic máy.

Windows hiện liệt kê `Microphone Array (AMD Audio Device)` và `Microphone (DeskIn(R) Virtual Audio Device)` đều `OK`. Bộ chọn code ưu tiên AMD và tránh DeskIn nếu chưa được chọn rõ. Trạng thái `OK` chỉ xác nhận Windows nhận thiết bị, chưa xác nhận trình duyệt đang thu đúng mic hoặc giọng thật được chép đúng. Trong lượt này không có người đọc câu kiểm thử vào mic, nên mình không tự nhận mic đã đạt WER hay sửa được lỗi thu âm vật lý. Audio FLEURS để tải/chạy thử: [10057024225045006889.wav](../../.tmp/fleurs_vi_test_samples/10057024225045006889.wav).

Kết quả JSON: `evaluation/results/live_groq_stt_fleurs_fullpath_retest_20261021.json`.

Kiểm tra hồi quy sau lượt này: backend `pytest -q` **901 passed**; kiosk `npm run test:run` **64 passed**; `npm run build` thành công; Ruff `backend evaluation tests` qua. Windows trả trạng thái thiết bị `OK` cho AMD và DeskIn, còn endpoint STT và trang demo được kiểm tra riêng bằng các lượt HTTP ở trên.

## Chạy lại theo yêu cầu tìm audio công khai và tự kiểm tra mic — 21/10/2026

Không cần bản thu riêng: lấy lại 12 WAV tiếng Việt có transcript đối chiếu từ Google FLEURS (`vi_vn`, `test`, CC BY 4.0), tạo session mới và gửi qua đúng endpoint demo `POST /audio/transcribe`. Kết quả hiện tại: **12/12** được nhận, **4/12** khớp nguyên văn, WER gộp **8,83%**, CER **5,01%**, độ trễ endpoint median/p95 **1.172/3.141 ms**. Cả 12 clip dùng `whisper-large-v3`; không có request lỗi hay audio bị từ chối. Kết quả khớp WER/CER lần chạy 12 mẫu trước đó; độ trễ dao động theo mạng và provider. Transcript chuẩn và kết quả từng mẫu nằm trong `evaluation/results/live_groq_stt_fleurs_current_endpoint_20261021.json`.

Mẫu WAV để tải và thử: [10057024225045006889.wav](../../.tmp/fleurs_vi_test_samples/10057024225045006889.wav). Nguồn dữ liệu: [Google FLEURS](https://huggingface.co/datasets/google/fleurs). Đây là giọng đọc sạch; không đại diện hội thoại tự nhiên, giọng địa phương, phòng ồn hoặc mic máy.

Kiểm tra trực tiếp tab demo sau khi mở session mới: trình duyệt chọn `Microphone Array (AMD Audio Device)`. Để mic nghe 9 giây khi không chủ động nói, meter quanh **0,00019**; giao diện hiện cảnh báo tín hiệu gần như không có, không tạo transcript, không gọi agent. Đã dừng capture và xác nhận nút trở về `Bắt đầu nói`. Điều này chứng minh nhánh im lặng không còn tự sinh bubble trong lần thử; không chứng minh được nhận dạng giọng nói qua mic AMD vì không có câu nói thật đi vào mic.

Chạy lại TTS qua hai endpoint demo: tổng hợp đầy đủ và ticket stream Azure thành công **3/3**. Với ba câu tiếng Việt cửa hàng, thời gian đến chunk audio đầu median/p95 **187/257 ms**, hoàn tất stream **373/391 ms**, tổng hợp đầy đủ median **1.392 ms**. Kết quả: `evaluation/results/live_azure_tts_current_endpoint_20261021.json`. Đây là độ trễ API; chưa đo thời gian loa phát hoặc độ tự nhiên bằng người nghe.

Transcript outro lặp được nêu trước đó có guard phía server và frontend; test riêng xác nhận đoạn đó bị loại, còn lặp từ tự nhiên trong yêu cầu mua hàng được giữ. Vì chưa có WAV thu từ micro khi đoạn lặp phát sinh, chưa thể kết luận Whisper sai từ âm thanh nào trong trường hợp cũ. Nếu AMD tiếp tục báo meter gần 0 khi bạn nói trực tiếp, vấn đề nằm trước STT (đầu vào/driver/đường capture); hiện không có phép thử tự động nào thay thế được một câu nói thật qua chính mic đó.

## So sánh model trên cùng audio người thật và sửa over-correction danh mục — 21/10/2026

Đối chiếu `whisper-large-v3` với `whisper-large-v3-turbo` trên đúng 12 WAV có transcript của bộ test tiếng Việt Google FLEURS (`vi_vn`). Hai model nhận cùng byte audio, cùng prompt bán lẻ, language `vi`, temperature `0`, kiểm tra tín hiệu và hậu xử lý danh mục. Không bật model fallback trong lượt so sánh để giữ rõ model nào tạo transcript. Nguồn: [Google FLEURS](https://huggingface.co/datasets/google/fleurs), giấy phép CC BY 4.0.

| Model | Nhận audio | Khớp nguyên văn | WER / CER sau hậu xử lý | Median / p95 STT |
| --- | ---: | ---: | ---: | ---: |
| `whisper-large-v3` | 12/12 | 4/12 | 7,12% / 4,19% | 685 / 2.110 ms |
| `whisper-large-v3-turbo` | 12/12 | 3/12 | 8,83% / 5,34% | 616 / 900 ms |

Với 12 mẫu này, large-v3 chính xác hơn 1,71 điểm WER; turbo nhanh hơn 69 ms ở median và có p95 thấp hơn. Nên giữ large-v3 làm model chính và dùng turbo khi cần dự phòng/ưu tiên tốc độ; chênh lệch độ trễ nhỏ và p95 rất nhạy với cold start. Đây là clip đọc sạch, không phải hội thoại bán lẻ tự nhiên hoặc giọng thu từ mic AMD.

Benchmark ban đầu phát hiện hậu xử lý catalog làm WER large-v3 tăng từ 7,12% lên 8,83%. Hai lỗi cụ thể: `kế bên từ` bị sửa thành tên brand `Benew` vì từ `kế` mất dấu thành cue `kệ`; từ `gia` trong `gia tộc` kích hoạt đoán giá và biến `quyền lực` thành `giấy Xuyên Á thực`. Đã thay cue đơn từ mất dấu bằng cụm từ mua sắm rõ, chỉ nhận `giá` khi có dấu phù hợp, và chỉ dùng category gần candidate trong cùng câu. Hai test hồi quy bảo đảm `kế`/`quốc gia` không kích hoạt sửa brand; test bán lẻ `giá`, `còn hàng`, category và sửa `P/S` vẫn chạy qua.

Chạy lại cùng 12 WAV sau khi sửa: raw transcript large-v3 và transcript sau sửa danh mục giống nhau trên toàn bộ mẫu; WER/CER giữ ở 7,12% / 4,19%. Gửi thêm đủ 12 clip qua toàn endpoint FastAPI trên backend mới `127.0.0.1:8002`: nhận 12/12, 4/12 khớp nguyên văn, WER/CER 7,12% / 4,19%, median/p95 682 / 3.354 ms. p95 gồm lượt kết nối đầu lạnh 3.354 ms. Kết quả paired model: `evaluation/results/live_groq_stt_fleurs_model_ablation_postfix_20261021.json`; kết quả full endpoint: `evaluation/results/live_groq_stt_fleurs_current_endpoint_postfix_20261021_freshbackend.json`.

Kiểm tra `tests/test_catalog_transcription.py`: **17 passed**; toàn bộ backend: **905 passed**; Ruff trên service, test và benchmark qua. Demo frontend hiện đọc `VITE_API_BASE_URL=http://127.0.0.1:8002` và backend mới `/health` trả HTTP 200. Phần chat mở ra lấy session thành công; GET giỏ trả 503 vì cấu hình catalog là `snapshot`, còn giỏ sandbox chỉ bật với catalog demo được xác minh. Đây là giới hạn giỏ hàng, không ảnh hưởng endpoint tư vấn hoặc STT.

Giới hạn còn lại: chưa có lời nói thật đi qua mic của người dùng nên chưa tính WER giọng địa phương hoặc kiểm tra chính đường capture khi họ nói. FLEURS là dữ liệu đọc sạch; VIVOS trước đó có WER 10,23% trên 19 speaker và cho thấy độ chính xác thực tế còn cách xa hoàn hảo. Whisper vẫn xử lý từng đoạn sau khi VAD chốt câu, chưa stream chữ tạm thời trong lúc người dùng đang nói.

## Tự kiểm tra lại audio công khai và TTS stream — 21/10/2026

Tạo session mới trên backend demo `127.0.0.1:8002`, rồi gửi lần lượt 3 WAV tiếng Việt có transcript chuẩn từ Google FLEURS (`vi_vn`, test, CC BY 4.0). Kết quả: **3/3 HTTP 200**, **3/3 được nhận**, **2/3 khớp nguyên văn**, WER trung bình **1,19%**, CER **1,40%**, độ trễ endpoint median **1.065 ms**. Cả ba lượt dùng `whisper-large-v3`. Đây là ba câu đọc sạch, không đánh giá lời nói qua micro AMD hoặc giọng địa phương. JSON: `evaluation/results/live_groq_stt_fleurs_self_recheck_20261021.json`.

Kiểm tra lại Azure TTS bằng ticket stream trên cùng backend. Lượt lạnh trả chunk đầu sau **1.825 ms**, hoàn tất sau **2.546 ms**. Sau endpoint prewarm trả `warmed=true`, hai lượt stream đều HTTP 200 và có audio; chunk đầu **760/926 ms**, hoàn tất **910/1.203 ms**, nhận lần lượt **23.040/24.912 byte** qua 30/26 chunk. Độ trễ cao hơn các lượt trước, cho thấy biến thiên kết nối/provider đáng kể; streaming hoạt động nhưng không nên quảng bá các số nhanh trước đó như SLA. JSON: `evaluation/results/live_azure_tts_stream_self_recheck_20261021.json` và `evaluation/results/live_azure_tts_stream_self_recheck_prewarm_20261021.json`.

Regression sau cùng: backend STT/catalog/Azure/TTS ticket **75 passed**, frontend mic/VAD/transcript/voice **32 passed**, Ruff **passed**. Tab demo hiện chọn `Microphone Array (AMD Audio Device)` và frontend/backend `8002` đều trả HTTP 200. Không có người nói vào micro trong lượt này, nên không tính WER cho mic thật. Audio FLEURS dùng lại được tải ở `.tmp/fleurs_vi_test_samples/10057024225045006889.wav`; nguồn/giấy phép: [Google FLEURS](https://huggingface.co/datasets/google/fleurs).

## Tổng hợp audio người Việt công khai — 21/10/2026

Gộp hai lần đánh giá toàn endpoint trên audio đọc tiếng Việt có transcript chuẩn: 12 câu Google FLEURS và 19 câu VIVOS, tổng **31 câu / 566 từ tham chiếu**. `whisper-large-v3` nhận cả 31; **9/31** câu khớp nguyên văn; tổng **47 lỗi từ**, WER gộp **8,30%**, CER **4,40%**. FLEURS riêng đạt WER/CER **7,12% / 4,19%**; VIVOS **10,23% / 4,76%**.

Đây là chẩn đoán trên giọng người Việt đọc câu trong môi trường tương đối sạch, không phải hội thoại tự nhiên, thử giọng địa phương, thử phòng ồn hay phép đo mic của người dùng. WER/CER cũng tính cả khác biệt từ vựng/nội dung nên không đồng nghĩa tỷ lệ hiểu sai ý mua hàng. Audio mẫu FLEURS để tải và chạy lại: [10057024225045006889.wav](../../.tmp/fleurs_vi_test_samples/10057024225045006889.wav). Nguồn dữ liệu: [Google FLEURS](https://huggingface.co/datasets/google/fleurs) (CC BY 4.0) và [VIVOS trên Zenodo](https://zenodo.org/records/7068130) (CC BY-NC-SA 4.0; chỉ dùng để đánh giá phi thương mại).

## Tự tìm audio công khai và kiểm tra lại qua endpoint demo — 21/10/2026, 22:08 +07

Theo yêu cầu tự lấy mẫu, dùng ba WAV có transcript chuẩn từ Google FLEURS tiếng Việt (`vi_vn`, test, CC BY 4.0), tạo session mới và gửi qua đúng `POST /audio/transcribe` trên backend demo `127.0.0.1:8002`. Kết quả: **3/3 HTTP 200**, **3/3 speech detected**, **2/3 khớp nguyên văn**, WER trung bình **6,25%**, CER **1,61%**, median endpoint **912 ms**. Cả ba dùng `whisper-large-v3`.

Một câu đọc tên đội bóng `Springboks` thành `Spring Book` và “chuỗi thua” thành “chui thua”; hai câu còn lại khớp. Đây là ví dụ cụ thể cho lỗi từ đồng âm/tên riêng; bộ lọc nhiễu và loop guard không tự sửa được loại lỗi này nếu catalog không có bằng chứng. Kết quả được lưu tại [live_groq_stt_public_audio_self_test_20261021.json](../../evaluation/results/live_groq_stt_public_audio_self_test_20261021.json). Mẫu nguồn có thể tải lại tại [Google FLEURS](https://huggingface.co/datasets/google/fleurs); WAV đã tải nằm trong `.tmp/fleurs_vi_test_samples/`.

Kiểm tra tổng thể sau các thay đổi hiện có: **943 backend test passed**, **64 frontend test passed**, production build thành công; Ruff cho phần shopping mission mới và `git diff --check` đạt. Bản build vẫn báo chunk model 3D trên 500 kB.

Giới hạn: file FLEURS chỉ kiểm tra audio đã thu sẵn qua endpoint. Nó không tái tạo lời nói thật từ AMD microphone trên máy, nên không thể chứng minh vấn đề capture hoặc phương ngữ đã hết. Benchmark lớn hơn đã có trong các phần trước của báo cáo: 12 câu FLEURS và 19 speaker VIVOS. Nguồn VIVOS ghi rõ corpus được thu trong môi trường yên tĩnh và chỉ dùng học thuật; số liệu của VIVOS không đại diện hội thoại bán lẻ tự nhiên hoặc mic nhiễu ([VIVOS Zenodo](https://zenodo.org/records/7068130)).

## Test audio nhiều tỉnh và giảm thiên lệch tên thương hiệu trong prompt — 21/10/2026

Lấy 7 WAV có transcript và thông tin tỉnh từ preview [ViVoice-34](https://huggingface.co/datasets/anonymous-vivoice34/ViVoice34), giấy phép CC BY 4.0; mẫu gồm Hải Phòng, Quảng Ninh, Nghệ An, Thừa Thiên Huế, Quảng Trị, Gia Lai và Tiền Giang. Audio thuộc các đoạn đọc dài (tin tức, văn học, văn hóa), không phải hội thoại mua sắm tự nhiên. WAV đã chọn và file ghi nguồn/giấy phép nằm trong `.tmp/vivoice34_samples/vivoice34_province_samples.zip`.

Chạy 7 WAV qua endpoint FastAPI đầy đủ trên backend thử nghiệm đã nạp code mới (`POST /audio/transcribe`), gồm catalog vocabulary, Whisper quality gate và sửa từ theo catalog:

| Mẫu | WER trước prompt fix | WER sau prompt fix |
| --- | ---: | ---: |
| Hải Phòng | 5,23% | 4,58% |
| Quảng Ninh | 19,26% | 13,85% |
| Nghệ An | 33,33% | 32,68% |
| Thừa Thiên Huế | 32,47% | 31,17% |
| Quảng Trị | 52,91% | 51,74% |
| Gia Lai | 9,65% | 9,27% |
| Tiền Giang | 13,13% | 13,13% |

Gộp 7 câu: nhận diện 7/7, không có câu bị từ chối hay request lỗi; WER giảm từ **21,85%** xuống **20,26%**, CER từ **10,94%** xuống **9,47%**. Sau sửa, endpoint median/p95 **3.359/5.290 ms**, tất cả lượt dùng `whisper-large-v3`; không có mẫu khớp nguyên văn. Ba đoạn Nghệ An/Huế/Quảng Trị vẫn có WER cao, nên kết quả này chưa chứng minh nhận diện tốt giọng địa phương trong hội thoại.

Đối chiếu cùng 4 WAV khó phát hiện prompt cũ liệt kê sẵn `Coca Zero` có thể làm Whisper đổi “cậu cả” thành “Coca”. Đã bỏ tên nhãn hàng cố định khỏi prompt, giữ hướng dẫn tiếng Việt bán lẻ và thuật ngữ động từ catalog; thêm chỉ dẫn chỉ chép tên hàng khi nghe được trong audio. Trên 4 WAV, prompt bán lẻ không có ví dụ thương hiệu và có đủ 32 thuật ngữ catalog đạt WER **30,38%**, gần như không đổi so với prompt cũ; câu “cậu cả” được chép lại thành “cậu cả” và không còn chèn “Coca”. Prompt rất ngắn, thiếu ngữ cảnh bán lẻ, đạt WER **55,54%**. Vì vậy giữ ngữ cảnh bán lẻ và loại ví dụ thương hiệu cố định.

So sánh model trực tiếp trên cùng 4 WAV, cùng prompt mới và catalog vocabulary: `whisper-large-v3` WER **30,38%**; `whisper-large-v3-turbo` **30,70%**. Turbo có median **1.611 ms** so với **3.872 ms** của lượt large-v3 trực tiếp trong lần chạy này, nhưng không có lợi thế chính xác; giữ large-v3 làm model chính, turbo vẫn là dự phòng nhanh hơn. Bốn mẫu chưa đủ lớn để kết luận về mọi phương ngữ.

Kết quả lưu tại `evaluation/results/live_groq_stt_vivoice34_20261021.json` (trước sửa), `evaluation/results/live_groq_stt_vivoice34_after_promptfix_20261021.json`, `evaluation/results/live_groq_stt_vivoice34_prompt_ablation_20261021.json`, `evaluation/results/live_groq_stt_vivoice34_prompt_ablation_after_promptfix_20261021.json` và `evaluation/results/live_groq_stt_vivoice34_turbo_brandfree_20261021.json`.

Phép đo tab demo: capture mở được với `Microphone Array (AMD Audio Device)`; khi không có ai nói, meter ở mức **0,00019** và app hiện cảnh báo không có tín hiệu. Capture được dừng lại; không gửi câu rỗng sang agent. Không có câu nói thật đi vào mic trong lượt kiểm tra nên không thể báo WER cho mic vật lý. Các kết quả này đo audio công khai qua endpoint và không thay thế phép đo âm thanh đang đi vào mic của máy.

## Recheck trên backend demo sau prompt fix và đối chiếu ParrotGo — 21/10/2026

Đọc các ghi chú voice upgrade/latency và mã voice controller, STT alias, audio prefetch, output delivery của [ParrotGo](https://github.com/nvuoc/parrotgo). Các ý phù hợp với kiosk là quản lý chủ sở hữu lượt nói để chặn callback cũ, giữ pre-roll trước khi VAD chốt câu, chỉ phục hồi tên riêng từ alias có kiểm soát, phát TTS theo chunk, hủy stream khi người dùng ngắt, và đo first-audio riêng. Kiosk tiếp tục dùng Groq Whisper và Azure Speech; không đưa nghiệp vụ đặt xe hoặc LiveKit của ParrotGo vào ứng dụng.

Sau khi khởi động lại backend demo trên `127.0.0.1:8002`, chạy lại 7 audio ViVoice-34 qua đúng `/audio/transcribe`: **7/7 nhận**, **0 lỗi HTTP**, WER gộp **20,26%**, CER **9,47%**, median/p95 **3.869/6.309 ms**. Kết quả bằng lần thử backend cô lập trước đó. Câu “cậu cả” trong mẫu Quảng Trị không còn bị prompt gợi thành “Coca”, nhưng toàn transcript mẫu này vẫn có WER **51,74%**; chất lượng giọng đọc dài này cho thấy prompt chỉ gỡ được thiên lệch thương hiệu, không chữa được nhận diện phương ngữ yếu. Audio là lời đọc có transcript, không phải đối thoại mua hàng.

Chạy lại hai ca chống transcript bịa qua backend demo. WAV im lặng số hóa 2 giây trả `speech_detected=false`, text rỗng trong **33 ms**, không cần gọi Whisper. Câu lặp do người dùng báo được tổng hợp bằng Azure rồi gửi qua Whisper; API trả text rỗng và `speech_detected=false` trong **775 ms** nhờ kiểm tra vòng lặp transcript. Điều này xác nhận ca im lặng và câu lặp cụ thể không được chuyển tiếp sang agent trên code hiện tại; không chứng minh mọi loại tiếng ồn hoặc mọi transcript sai đều bị phát hiện.

Đo TTS qua đúng đường kiosk `POST /audio/synthesize/tickets` → stream một lần trên cùng backend. Cả **3/3** lượt trả audio MP3 qua Azure `vi-VN-HoaiMyNeural`, mỗi lượt nhận 47–54 chunk. Lượt lạnh: chunk đầu **1.984 ms**, stream xong **2.647 ms**. Sau prewarm thành công (**622 ms** chạy nền), hai lượt còn lại có chunk đầu **249/272 ms**, hoàn tất **406/459 ms**. Median trên cả ba lượt là **272 ms** tới chunk đầu và **459 ms** tới hết; p95 mẫu nhỏ bị lượt lạnh chi phối (**1.984/2.647 ms**). Streaming hoạt động; thời gian khởi tạo/provider vẫn dao động.

Kết quả mới nằm tại `evaluation/results/live_groq_stt_vivoice34_demo_recheck_20261021.json`, `evaluation/results/live_stt_hallucination_guard_demo_recheck_20261021.json` và `evaluation/results/live_azure_tts_ticket_demo_recheck_20261021.json`. Có thể chạy lại bằng `evaluation/live_groq_stt_vivoice34_benchmark.py`, `evaluation/live_stt_hallucination_guard_recheck.py` và `evaluation/live_azure_tts_ticket_benchmark.py`.

Validation sau thay đổi: **947 backend tests passed**, **64 frontend tests passed**, production build thành công, Ruff và `git diff --check` đạt. Cảnh báo build duy nhất là chunk model 3D vượt 500 kB. Demo frontend `5173` và backend `8002` đang chạy với code mới. Meter AMD lúc im lặng vẫn ở gần **0,00019**; không có lời nói thật đi vào mic trong lần này, vì vậy chất lượng capture trên mic vật lý của người dùng vẫn chưa được đo.

## Recheck STT/TTS trên demo — 21/10/2026

Chạy ba clip đầu của Google FLEURS tiếng Việt (`vi_vn`, test, CC BY 4.0) qua đúng backend demo `127.0.0.1:8002` và toàn endpoint `/audio/transcribe`. Cả **3/3** trả HTTP 200, dùng `whisper-large-v3` và được nhận; **1/3** khớp nguyên văn. WER từng clip là **0%, 12,5%, 8%**; độ trễ **1.208, 828, 619 ms** (median **828 ms**). Đây là audio đọc sẵn, không phải thu từ micro của người dùng.

Chạy lại luồng TTS ticket → stream trên cùng demo: **2/2** lượt thành công, dùng Azure `vi-VN-HoaiMyNeural`, nhận đủ **44.352 byte** audio mỗi lượt. Chunk đầu median/p95 **462/646 ms**; stream hoàn tất **654/881 ms**. Mẫu hai lượt còn nhỏ; thời gian lần đầu cao hơn lần sau.

Kiểm tra sau đó: **391 backend test** cho Groq STT, hiệu chỉnh catalog và Retail API; **32 frontend test** cho transcript, mic/VAD và luồng voice; production build thành công; Ruff thành công. Build còn cảnh báo bundle model 3D khoảng **1 MB**. Phép thử này xác nhận endpoint và streaming; nó không đo WER từ giọng nói thu trực tiếp qua microphone.
