"""Assemble measured artifacts and explicitly authored, non-blind review notes."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "docs/reports"
A = REPORTS / "artifacts"

# Development annotations made after reading the actual replies. Never runtime policy.
REVIEWS = [
    ("weak", "Hỏi đồ gì quá chung; nên đưa hướng bếp/vệ sinh/cá nhân để khách dễ chọn."),
    ("fail", "Hỏi khách có muốn được đề xuất dù chính khách vừa nhờ tư vấn; chưa tiến bước."),
    ("fail", "Rơi vào câu trả lời dự phòng, chưa tư vấn được."),
    ("reasonable_first_action", "Hỏi tuổi trẻ có ích; cách nói trẻ bạn còn gượng."),
    (
        "reasonable_first_action",
        "Thiếu sở thích của mẹ; hỏi tiếp hợp lý nhưng có thể đưa hướng chọn.",
    ),
    ("fail", "Câu trả lời dự phòng, chưa chọn được đồ ăn tiện."),
    ("weak", "Hỏi khu vực có ích nhưng chưa đưa bộ đồ dọn tối thiểu hoặc hướng cụ thể."),
    ("weak", "Có số người/bữa/ngân sách nhưng tiếp tục đẩy việc chọn thực đơn cho khách."),
    ("fail", "Câu trả lời dự phòng cho nhu cầu dầu gội dưới giá."),
    ("reasonable_first_action", "Hỏi số người và bữa cần mua trước khi tính cả tuần."),
    ("reasonable_first_action", "Phiên mới thiếu nhóm hàng, cần hỏi nhóm/ngân sách."),
    ("reasonable_first_action", "Phiên mới thiếu món gốc nên chưa thể tìm hàng rẻ hơn."),
    ("fail", "Câu trả lời dự phòng cho nhu cầu giá trị chưa rõ nhóm."),
    ("reasonable_first_action", "Chưa có hai SKU trong phiên mới, cần làm rõ."),
    ("reasonable_first_action", "Hỏi dạng nước/bột trước khi so hai hãng; chưa bịa hiệu quả."),
    ("reasonable_first_action", "Thiếu sản phẩm đang nhắc ở phiên mới."),
    ("reasonable_first_action", "Thiếu cặp sản phẩm đang so ở phiên mới."),
    ("reasonable_first_action", "Thiếu nhóm hàng, không tự bịa trải nghiệm cá nhân."),
    ("reasonable_first_action", "Thiếu nhóm/cặp để so cùng mức giá."),
    ("fail", "Câu trả lời dự phòng cho da nhạy cảm."),
    ("fail", "Suy ra vị ngọt nhẹ từ không đường; gán giàu chất xơ cho cả hai SKU thiếu căn cứ."),
    ("weak", "Hỏi nhóm vệ sinh có ích nhưng hỏi lại yêu cầu an toàn mèo khách đã nêu."),
    ("weak", "Hỏi loại đồ uống nhưng chưa chủ động lựa chọn có ghi không/ít đường."),
    ("reasonable_first_action", "Chỉ biết phòng nhỏ, thiếu hẳn món cần mua."),
    ("fail", "Tự giả định là lăn/xịt khử mùi dù khách chưa nói nhóm hàng."),
    ("fail", "Câu trả lời dự phòng cho vị trí nước giặt; đã sửa định tuyến sau run này."),
    ("weak", "Hỏi mua cho ai chưa đủ phân biệt sữa màu xanh; cần thông tin nhận diện có ích hơn."),
    ("fail", "Câu trả lời dự phòng cho mì Hàn lần mua trước."),
    ("fail", "Câu trả lời dự phòng cho vị trí khăn giấy; đã sửa parser/định tuyến sau run này."),
    ("reasonable_first_action", "Có 1 lít nhưng phiên mới chưa biết nhóm hàng."),
    ("weak", "Chỉ xác nhận lại ý thay thế, chưa hỏi món gốc để tìm."),
    ("reasonable_first_action", "Quảng cáo TikTok không đủ nhận diện, cần nhóm/tên."),
    ("reasonable_first_action", "Tốt vừa tiền nhưng chưa biết loại hàng."),
    ("reasonable_first_action", "Cả nhà chưa xác định sản phẩm, câu hỏi có hướng nhóm."),
    ("weak", "Câu hỏi màu vàng nào còn vòng, cần hỏi nhóm/hãng/dấu hiệu nhận diện."),
]


def read(name):
    return json.loads((A / name).read_text(encoding="utf-8"))


def main():
    full = read("customer_consultation_35_grounded_review_20261101.json")
    metrics = read("consultation_voice_measured_summary_20261101.json")
    journeys = read("customer_consultation_journeys_voice_delivery_20261101.json")
    lookup = read("customer_consultation_35_lookup_delivery_20261101.json")
    tts = read("voice_pipeline_tts_20261101.json")
    e2e = read("voice_pipeline_e2e_20261101.json")
    assert len(full["rows"]) == len(REVIEWS) == 35
    notes = [
        {"index": row["index"], "verdict": verdict, "note": note}
        for row, (verdict, note) in zip(full["rows"], REVIEWS, strict=True)
    ]
    counts = dict(Counter(row["verdict"] for row in notes))
    (A / "customer_consultation_35_grounded_review_manual_20261101.json").write_text(
        json.dumps(
            {
                "reviewer": "Codex agent, non-blind self-review",
                "counts": counts,
                "meaning": "Reasonable first action is not a completed consultation "
                "or employee-quality score.",
                "rows": notes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    table = "\n".join(f"| {n['index']} | {n['verdict']} | {n['note']} |" for n in notes)
    tts_table = "\n".join(
        f"| {r['case']} | {r['voice']} | {r['first_audio_ms_including_ticket']} | "
        f"{r['complete_ms']} | "
        f"{r['duration_seconds']} | {r['reuse_status']} |"
        for r in tts["rows"]
        if r.get("kind") == "real-ticket-stream-decode"
    )
    journey_table = "\n".join(
        f"| {j['name']} | {len(j['turns'])} | "
        f"{sum(bool(t.get('response', {}).get('products')) for t in j['turns'])} |"
        for j in journeys["journeys"]
    )
    lookup_table = "\n".join(
        f"| {r['index']} | {r['elapsed_seconds']} | {r['response']['model']} | "
        f"{len(r['response']['products'])} |"
        for r in lookup["rows"]
    )
    appendix = "\n\n".join(
        f"### Câu {row['index']}\n\nKhách: {row['query']}\n\n"
        f"Trả lời thực tế: {row['response']['text']}\n\n"
        f"Model: `{row['response']['model']}`. Thời gian: {row['elapsed_seconds']} s."
        for row in full["rows"]
    )
    f, v = metrics["fleurs"], metrics["vivoice"]
    c = metrics["consultation"]
    fallback_count = c["models"].get("consultation:consultation-fallback", 0)
    vad_counts = metrics["vad"]
    stt_table = "\n".join(
        f"| {label} | {m['accepted']}/{m['samples']} | {m['exact_matches']} | "
        f"{m['aggregate_wer']:.2%} | {m['aggregate_cer']:.2%} | "
        f"{m['median_ms']:g} / {m['max_ms']} |"
        for label, m in [("FLEURS vi_vn test", f), ("ViVoice34, 7 tỉnh metadata", v)]
    )
    text = f"""# Báo cáo kiểm tra tư vấn và giọng nói — 01/11/2026

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

Tự rà: **{counts["reasonable_first_action"]} hành động đầu hợp lý, {counts["weak"]} yếu,
{counts["fail"]} lỗi**. Hợp lý ở lượt đầu thường chỉ là hỏi đúng thông tin còn thiếu,
không phải đã tư vấn xong. Có {fallback_count}
lượt fallback; chỉ {metrics["consultation"]["rows_with_products"]}/35 có thẻ sản phẩm.
Median {c["median_seconds"]} s, max {c["max_seconds"]} s.
Qwen bị giới hạn, kết quả chủ yếu từ gpt-oss-120b/20b dự phòng. Không gộp lỗi provider
với bằng chứng mô hình không hiểu ngữ nghĩa, cũng không bỏ chúng khỏi trải nghiệm khách.

Điểm rõ nhất: case 21 vẫn suy ra vị ngọt từ không đường và gán chất xơ cho cả hai món.
Reviewer chấp thuận sai. Các câu không rõ nhóm có thể hỏi tiếp, nhưng nhu cầu đã đủ rõ
lại hay bị hỏi ngược. Không có bằng chứng đợt thử này tốt hơn đợt 17 hợp lý/9 yếu/9 lỗi trước.

| Câu | Đánh dấu của agent | Nhận xét sau khi đọc đáp án |
| --- | --- | --- |
{table}

Nguồn: [35 đáp án gốc](artifacts/customer_consultation_35_grounded_review_20261101.json),
[annotation thủ công](artifacts/customer_consultation_35_grounded_review_manual_20261101.json).

### Kiểm tra lại sau sửa

Sáu câu 3/9/20/21/26/29 được chạy lại sau sửa định tuyến/cooldown. 9/20/21 vẫn fallback;
3 thành hỏi thêm. 26 đi qua catalog được; 29 lộ lỗi phủ định tiếng Việt, sau đó được sửa.
Đây không phải một lần chạy lại đủ 35 câu.

Replay cuối của hai câu tra vị trí:

| Câu | Thời gian s | Controller | Thẻ hàng |
| --- | --- | --- | --- |
{lookup_table}

Case 26 trả kệ theo sơ đồ đề xuất và nói chưa xác minh vị trí thật, nhưng còn dài và
nhiều mã kệ/quy cách. Case 29 bỏ được lỗi fallback/phủ định nhưng **vẫn trả gợi ý hàng,
không trả vị trí**; case này vẫn chưa đạt. Không lấy hai HTTP 200 làm hai lượt tư vấn tốt.
Nguồn: [6 câu sau sửa](artifacts/customer_consultation_35_voice_delivery_checks_20261101.json),
[2 câu tra cứu cuối](artifacts/customer_consultation_35_lookup_delivery_20261101.json).

### Hội thoại nhiều lượt

Chạy lại sáu journeys, tổng {sum(len(j["turns"]) for j in journeys["journeys"])} lượt ở
phiên bản sau cooldown/định tuyến, trước parser cuối. Các chuỗi vẫn cho thấy hỏi vòng,
không chốt được danh sách, hỏi lại loại bò sau khi khách đã nói thái lát. Không lấy việc
HTTP 200 hoặc nhớ được từ sữa làm bằng chứng hội thoại tốt.

| Journey | Lượt đã chạy | Lượt có thẻ hàng |
| --- | --- | --- |
{journey_table}

[Toàn bộ journeys](artifacts/customer_consultation_journeys_voice_delivery_20261101.json).

## STT với giọng người thật

Gọi endpoint đang chạy, Groq Whisper large-v3 thực tế; WAV công khai có tham chiếu và
giấy phép CC-BY-4.0. WER/CER dùng normalization của harness, là tỷ lệ tổng lỗi/tổng từ
hoặc ký tự tham chiếu, không phải trung bình phần trăm từng mẫu.

| Bộ diagnostic đã lộ | Mẫu được nhận | Exact theo normalization | WER | CER | Median / max ms |
| --- | --- | --- | --- | --- | --- |
{stt_table}

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

- {vad_counts["human_files_with_real_speech"]}/{vad_counts["human_files"]} file người thật
  có SPEECH_REAL_START.
- Ba control im lặng/tone/nhiễu trắng và đoạn người nói cắt ngắn không phát event speech.
- Có {metrics["vad"]["segments_over_12_seconds"]} segment dài hơn 12 s. Controller sẽ từ chối;
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
{tts_table}

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

{appendix}
"""
    (REPORTS / "consultation_voice_audit_20261101.md").write_text(text, encoding="utf-8")
    files = [
        "backend/services/customer_consultation.py",
        "backend/services/groq.py",
        "backend/services/query_understanding.py",
        "backend/api/main.py",
        "apps/kiosk-airi/src/voice-controller.ts",
        "apps/kiosk-airi/src/voice-neural.ts",
        "data/product_knowledge_enrichment_pilot.json",
        "pyproject.toml",
        "uv.lock",
    ]
    (A / "consultation_voice_delivery_manifest_20261101.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "release_status": "experimental, not accepted as employee-quality",
                "source_sha256_at_report": {
                    file: hashlib.sha256((ROOT / file).read_bytes()).hexdigest() for file in files
                },
                "version_scope": "Full35, STT, TTS, journeys and final lookup have per-run "
                "hashes; do not pool as one final-version run.",
                "physical_microphone_and_browser_playback": "not verified in this run",
                "livekit": "not integrated",
                "verification": {
                    "earlier_full_backend": "1638 passed, 1 Windows symlink skip",
                    "final_affected_logic": "252 passed",
                    "final_api_intent_search": "561 passed",
                    "final_frontend": "101 passed, production build succeeds",
                    "lint": "passed for audited files",
                    "counts_overlap": True,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "manual_counts": counts,
                "report_written": True,
                "journeys": len(journeys["journeys"]),
                "journey_turns": sum(len(j["turns"]) for j in journeys["journeys"]),
                "e2e_rows": len(e2e["rows"]),
            }
        )
    )


if __name__ == "__main__":
    main()
