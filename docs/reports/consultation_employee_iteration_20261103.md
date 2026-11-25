# Vòng hoàn thiện tư vấn kiểu nhân viên — 03/11/2026

## Trạng thái

Bản này **chưa được xác nhận đạt mức nhân viên thật**. Hai lượt tư vấn live cuối
đọc được vẫn yếu và sáu lượt bị provider từ chối; provider đã báo hết quota theo
ngày. Vì vậy không dùng HTTP 200, số thẻ hàng, hay số test để gọi là đạt chất
lượng hội thoại. Mục tiêu của vòng này là loại bỏ những lỗi có thể tái hiện trong
mã và tạo phép kiểm chứng để lần live tiếp theo không lặp lại chúng.

## Đã sửa sau probe v6

### Không cho bản sửa chưa được duyệt quay lại lượt chọn

Probe v6 cho thấy reviewer đánh dấu nội dung về sản phẩm là không có căn cứ,
nhưng vẫn trả `revised_text`; lượt retry đã chép phần văn đó và giữ nguyên các
khẳng định như giảm dầu, làm dịu hay hỗ trợ trị mụn. Vòng lặp giờ chỉ chuyển
`issue` và `product_checks` vào retry. `revised_text`, câu hỏi và trạng thái đề
xuất bị bỏ khi review không đạt. Selector phải tự sửa từ candidate facts và bị
kiểm tra lại ở biên renderer/reviewer.

### Tách lựa chọn thay thế khỏi giỏ mua chung

Các sản phẩm trong một yêu cầu chọn loại hàng là các phương án, không phải hai món
phải mua cùng. Renderer giờ bác các câu kiểu “kèm theo”, “mua cả hai”, “kết hợp
hai loại” trong chế độ alternatives; chế độ basket vẫn cho phép ghép nhiều vai
trò và tính tổng. Kiểm tra dùng nhóm hàng tổng hợp Sổ, Khăn và Đèn để tránh gắn
logic vào một ngành hàng.

### Đo độ đầy đủ ngữ cảnh taxonomy

`evaluation/audit_consultation_context.py` kiểm tra toàn bộ **346 đường dẫn** và
**463 nhãn**. Mỗi nhãn đã biết giữ lại tất cả đường dẫn con; scope không xác định
vẫn giữ toàn cây. Context tập trung có trung vị 12 đường dẫn/938 ký tự so với
28.070 ký tự của toàn cây. Đây là phép đo kích thước context, không phải token
provider, recall hay điểm tư vấn.

Artifact: [employee_v7_context_20261103.json](artifacts/employee_v7_context_20261103.json).

### Provenance của evaluator

Evaluator live giờ hash cả `backend/services/retail_tools.py`, tránh ghi nhận một
phiên chạy mà helper truy xuất đã đổi nhưng manifest không biết. Bản ghi quota chỉ
giữ cờ loại lỗi (`tokens per day`, `json_validate_failed`...), không ghi response
thô hay credential.

## Bằng chứng kiểm thử

- Audit catalog: **3.081 sản phẩm / 346 đường dẫn**, 12.357 kiểm tra truy xuất và
  ràng buộc qua, 0 lỗi. Snapshot chỉ là dữ liệu lưu; không chứng minh tồn kho hiện
  tại.
- Frontend hiện có **102 test** và build qua từ vòng trước. LiveKit chưa được tích
  hợp runtime; phần voice hiện vẫn là VAD trình duyệt + HTTP WAV STT + SSE + vé
  Azure TTS một lần. Không gọi đó là nghiệm thu micro/loa hay LiveKit.
- Regression mới bao phủ reviewer rejection, retry không nhận `revised_text`
  chưa duyệt, alternatives không bị viết thành basket, basket vẫn hoạt động, và
  state clarification cũ. Ruff qua trên các file sửa.
- Full backend v7: **1.989 passed, 1 skipped, 1 warning** trong 380,26 giây, được
  ghi tại [employee_v7_backend_20261103.txt](artifacts/employee_v7_backend_20261103.txt).
  Nếu provider test không liên quan bị thay đổi thì đọc số cuối trong artifact,
  không suy diễn chất lượng lời tư vấn từ nó.

## Nghiên cứu và cách chuyển thành kỹ thuật

- [ProductAgent / PROCLARE, EMNLP Industry 2025](https://aclanthology.org/2025.emnlp-industry.25/):
  pool ứng viên, tóm tắt facet và memory có cấu trúc. Dự án áp dụng typed
  consultation state, bounded candidate facts và context theo family; chưa huấn
  luyện lại hoặc nhận kết quả mô phỏng của paper.
- [ChatCRS, Findings of NAACL 2025](https://aclanthology.org/2025.findings-naacl.17/):
  tách goal planning khỏi knowledge retrieval. Dự án giữ planner/selector/reviewer
  riêng và truyền retrieval observation; chưa có planner model được train như paper.
- [PC-CRS, Findings of EMNLP 2024](https://aclanthology.org/2024.findings-emnlp.247/):
  critique phải đối chiếu chính item và bản nháp. Dự án đã thêm product checks và
  retry từ draft thật; vòng này sửa lỗi lấy nhầm editorial revision chưa duyệt.
- [PEPPER, Findings of EMNLP 2025](https://aclanthology.org/2025.findings-emnlp.1067/):
  đánh giá quá trình hình thành sở thích thay vì đoán một target item. Bộ test
  hiện vẫn là development probes; chưa có simulator target-free hay chấm mù.
- [ASK, ACL Industry 2025](https://aclanthology.org/2025.acl-industry.63/):
  route giữa show-result, top-k clarification và domain clarification. Dự án
  mượn nguyên tắc “hỏi khi câu trả lời đổi quyết định”, không bê bộ phân loại hoặc
  kết quả paper vào runtime.
- [MG-ShopDial](https://github.com/iai-group/MG-ShopDial): 64 hội thoại người–người,
  2.196 utterance theo README. Bản dữ liệu công khai có SHA-256
  `9e879293623a12302fa9aeb888bf20b7c52f71a17904e5fd36922c58335f0ab5`; dùng làm
  khung thiết kế lượt đổi ý/hỏi–đáp, không dùng làm SKU oracle hay dữ liệu tiếng Việt.

Các paper đều gợi ý cùng một nguyên tắc: retrieval, trạng thái hội thoại và câu
trả lời phải được đánh giá riêng; strict JSON chỉ kiểm tra hình thức. Chưa thực
hiện SFT, preference tuning, reranker dense có nhãn, hay thu thập hội thoại nhân
viên Việt Nam. Đây là khoảng trống chất lượng lớn nhất hiện nay.

## Những gì còn thiếu để gọi là nhân viên thật

1. Chờ quota/provider ổn định rồi chạy một phiên source cố định đủ các journey:
   đồ ăn sáng, lẩu, giặt, pin, dọn nhà, gối, da mụn, thú cưng và đổi ý. Đọc từng
   câu, tính cả lượt fallback và unavailable; không loại chúng khỏi mẫu.
2. Tạo tập query–product tiếng Việt độc lập có nhãn **đúng chức năng / thay thế /
   phụ trợ / lạc nhóm**, chia train/dev/test trước khi sửa prompt. Đo recall,
   hard-negative và calibration; không dùng chính 35 câu phát triển làm test mù.
3. Thu thập hội thoại được phép sử dụng với nhân viên thật, cho hai người chấm mù
   theo mục đích, lý do chọn, giữ ràng buộc, câu hỏi cần thiết, tự nhiên và tiến
   triển. Báo cáo độ đồng thuận và các lỗi nghiêm trọng riêng.
4. Chỉ sau hai bước trên mới thử reranker/SFT/preference tuning. Mỗi thay đổi phải
   có ablation để biết cải thiện đến từ retrieval, policy hay văn phong.
5. Với voice, đo lại STT/WER tiếng Việt theo giọng thực tế, VAD false-cut/late-cut,
   TTS latency và replay; sau đó mới tích hợp/kiểm tra LiveKit room, participant,
   interruption và semantic turn detector. Readiness document hiện là nghiên cứu,
   không phải runtime pass.

Báo cáo chất lượng chi tiết vẫn ở
[consultation_human_quality_20261103.md](consultation_human_quality_20261103.md),
đánh giá từng lượt ở [consultation_human_manual_review_20261103.md](consultation_human_manual_review_20261103.md).
