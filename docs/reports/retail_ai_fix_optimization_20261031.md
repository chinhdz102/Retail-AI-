# Kiểm thử, sửa lỗi và tối ưu Retail-AI — 31/10/2026

Đã đối chiếu báo cáo production quality, semantic intelligence V2, generalized semantic
consultation và nghiên cứu retrieval cập nhật ngày 29/10. Đợt này sửa giới hạn thời gian
đọc PostgreSQL và giảm tìm kiếm lặp trên snapshot. Trạng thái phát hành vẫn **demo-only**:
dữ liệu cửa hàng và đánh giá độc lập còn thiếu.

Code nền: `20ed433b99efa9cff740ce2681324a88ee4e498f`, nhánh
`codex/retail-text-quality-v4-20261019`. Kiểm thử trên working tree có thay đổi;
không phải kết quả của riêng commit HEAD. Các chỉnh sửa worker/timeout có sẵn được giữ lại,
kiểm tra và hoàn thiện. [Manifest mã nguồn](artifacts/verified_source_manifest_20261031.json)
ghi SHA-256 của cả mã nguồn chưa được Git theo dõi.

## Vấn đề và thay đổi

| Vấn đề | Xử lý / trạng thái |
| --- | --- |
| Timeout PostgreSQL chỉ mới áp dụng cho tìm kiếm; đọc chi tiết, taxonomy, thương hiệu và các đường đọc khác còn thiếu giới hạn câu lệnh. | Dùng chung cấu hình kết nối/câu lệnh cho cả 8 đường đọc catalog; bổ sung cho thông tin chi nhánh. Giới hạn cả bước đóng kết nối. |
| Cùng truy vấn và điều kiện lọc bị tìm, chấm điểm lại trên catalog chỉ đọc. | Cache LRU tối đa 256 trang cho từng repository; khóa gồm đầy đủ truy vấn, giá, thương hiệu, danh mục, tồn kho, độ cay, thứ tự và phân trang. Trả danh sách mới để caller không sửa cache. Nạp lại repository tạo cache mới. |
| Hủy/timeout request không dừng được Python thread đang chạy. | Xác minh thay đổi bounded worker có sẵn: giữ slot đến khi worker thật sự kết thúc; quá tải trả lỗi tạm thời. Thêm regression cho hủy request, phục hồi sau exception và cô lập context giữa request. |
| Catalog sai/thiếu dữ kiện: `Nhộng tằm` nằm trong nhóm cá; phần lớn sản phẩm thiếu thuộc tính độ phù hợp; tồn kho/giá là snapshot 15/09. | Chưa giải quyết bằng dữ liệu mới. Cần chỉnh nguồn có kiểm duyệt và feed cửa hàng; không suy ra công dụng hoặc tồn kho hiện tại từ benchmark. |
| Chưa có qrels/đánh giá câu trả lời độc lập hoàn tất và bộ blind được bảo vệ. | Vẫn là điều kiện chưa đạt trước production; điểm synthetic không thay thế review con người. |

Các cấu hình worker/driver được giải thích trong [README](../../README.md) và
`.env.example`. Mặc định 8 worker, tối đa 16 công việc đang chạy/chờ mỗi process;
kết nối PostgreSQL 2 giây, mỗi lệnh và đóng kết nối 2,5 giây. Đây là giới hạn từng
thao tác, không phải cam kết toàn bộ request hoàn thành trong 2,5 giây.

## Kiểm tra

- Backend trước thay đổi bổ sung: **1.527 passed, 1 skipped** trong 232,71 giây.
- Backend cuối: **1.551 passed, 1 skipped** trong 225,59 giây; mã nguồn không đổi
  trong suốt lượt xác minh cuối.
- Frontend: **100 test / 9 file qua**, TypeScript/Vite build qua. Build còn cảnh báo
  chunk `model-viewer` khoảng 1,02 MB minified; chưa có đo trình duyệt/voice mới.
- Bộ tập trung catalog/tool/agent: **207 passed**; sau khi bổ sung kiểm tra hủy/context,
  bounded worker và PostgreSQL timeout: **25 passed**.
- Ruff, Python compile và `git diff --check`: qua; Git có cảnh báo chuyển đổi LF/CRLF.
- Một test symlink bị bỏ qua vì Windows không cấp quyền tạo link (`WinError 1314`).

Log: [backend ban đầu](artifacts/pytest_baseline_20261031.log),
[backend cuối](artifacts/pytest_final_20261031.log),
[test tập trung](artifacts/pytest_focused_20261031.log),
[frontend build](artifacts/frontend_build_20261031.log).
Thông tin môi trường, checksum và tổng hợp lệnh:
[quality gate](artifacts/quality_gate_optimization_20261031.json).

Benchmark frozen chạy lại gồm **300 câu + 50 hội thoại / 200 lượt**. Khi bỏ các trường
thời gian, toàn bộ kết quả từng case và hội thoại khớp artifact kiểm tra commit
`a4667c1`; thứ tự ID sản phẩm và trạng thái không đổi. Product R@1/R@5 và category P@5
vẫn 1,00 trên các câu sinh từ catalog; 100/100 case điều kiện được mã hóa qua, 0 vi phạm;
candidate Recall@5 vẫn **0,6878 trên 98 case có ứng viên hợp lệ**. Đây là kiểm tra hồi quy,
chưa đo độ phù hợp/ngôn ngữ tự nhiên theo đánh giá độc lập.

Artifacts: [kết quả frozen](artifacts/semantic_intelligence_v2_verified_20261031.json),
[so sánh output](artifacts/semantic_output_comparison_20261031.json).

## Độ trễ: kiểm tra bật/tắt cache trên cùng code

Chạy lần lượt **off → on → on → off**, cùng catalog 3.081 sản phẩm, cùng 4 câu,
20 request mỗi mức C1/C5/C20; tổng 240 request được tính thời gian. Trong chế độ off,
chỉ thay callable cache của instance bằng hàm tìm kiếm chưa cache. Parser vẫn có cache
riêng trong cả hai chế độ. Không chạy pytest/build đồng thời với phép đo này.

| Yêu cầu đồng thời | p95 cache tắt, 2 lần | p95 cache bật, 2 lần |
| ---: | ---: | ---: |
| 1 | 172,5–184,3 ms | 160,0–163,2 ms |
| 5 | 722,2–841,6 ms | 688,0–748,5 ms |
| 20 | 2.969,6–3.213,0 ms | 2.784,9–2.969,3 ms |

Cả bốn lần đều không có lỗi HTTP, không sai ID/tên/giá; mọi câu có thẻ sản phẩm,
thứ tự và giá khớp lần cache tắt đầu tiên. Cache giúp đường đọc lặp lại; mức cải thiện
API vừa phải và có dao động, chưa giải quyết hết độ trễ ở C20. Truy vấn chưa có trong
cache vẫn chạy thuật toán cũ. Các số trước/sau ban đầu và CPU profile được lưu riêng
để chẩn đoán, không dùng thay cho phép kiểm tra chéo cùng code này.

Tái lập: `uv run python docs/reports/artifacts/run_snapshot_cache_ablation_20261031.py`.
Nguồn/artifacts: [script](artifacts/run_snapshot_cache_ablation_20261031.py),
[off 1](artifacts/cache_ablation_off_1_20261031.json),
[on 1](artifacts/cache_ablation_on_1_20261031.json),
[on 2](artifacts/cache_ablation_on_2_20261031.json),
[off 2](artifacts/cache_ablation_off_2_20261031.json).

Phép đo dùng ASGI trong process và bốn truy vấn lặp, không có Groq/STT/TTS, browser,
proxy hay PostgreSQL thật. Timeout được xác minh bằng driver giả lập, gồm cả đường
lỗi và cleanup. Chưa có cơ sở kết luận SLA production hoặc chất lượng tư vấn tăng.

Ưu tiên tiếp theo là dữ liệu catalog có kiểm duyệt/feed cửa hàng, review qrels và
câu trả lời độc lập, rồi đo provider/browser/tải trên môi trường triển khai thực.
