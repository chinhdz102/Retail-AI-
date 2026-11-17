# Tối ưu độ trễ tư vấn — 2026-11-17

## Kết quả đã giữ lại

- Catalog snapshot được lập chỉ mục token một lần khi repository khởi động. Việc
  tìm hàng trong bộ 3.081 dòng không còn quét và chuẩn hóa lại toàn bộ dòng cho
  mỗi truy vấn.
- Client API dự phòng dùng chung connection pool của client chính. Lần gọi đầu
  không phải tạo lại nhiều TLS/client riêng cho từng key.
- Startup khởi tạo local speech, catalog và HTTP pool song song; không gửi request
  trả phí để warm-up.
- Các câu đã đủ cấu trúc từ catalog (xem nhóm hàng, lọc giá, đổi dung tích rõ
  ràng) đi qua renderer xác định. Code giữ ID, tên, giá, tồn và số lựa chọn;
  không gọi Planner/Composer/Reviewer chỉ để chép lại dữ liệu.
- Truy vấn Planner chỉ có placeholder như “sản phẩm/mặt hàng” kèm một thuộc tính
  không được phép mở rộng thành cả ngành hàng hoặc gọi sửa truy vấn. Điều này
  tránh trả ngẫu nhiên một SKU cùng quầy khi model chưa xác định loại hàng.
- Reviewer chỉ cần bốn cờ kiểm tra; chi tiết dài không còn được sinh cho các cờ
  đạt. Các lỗi cục bộ như `evidence_fields` thừa được sửa tại chỗ thay vì gọi lại
  model.

## Đo trên backend demo

Đo bằng `POST /agent/respond`, session mới cho mỗi câu, HTTP wall time trên máy
local. Đây là thời gian trả về toàn bộ JSON, không phải TTFT và không bao gồm
phát audio.

| Nhóm câu | Trước tối ưu, 1 mẫu | Bản cuối, 2 mẫu mỗi câu |
|---|---:|---:|
| Xem vài loại sữa rửa mặt | 0,83 s | 0,104–0,111 s |
| Đồ ăn sáng | 0,21 s | 0,093–0,094 s |
| Nước giặt dưới 150.000đ | 9,26 s | 0,102–0,105 s |
| Da dầu, nhiều mụn | 4,89 s | 2,70 s thành công; 5,37 s lỗi 502 |
| Thịt ăn lẩu | 7,45 s | 2,55–2,84 s |

Đây là mẫu local nhỏ, không phải p95 sản xuất hay thí nghiệm kiểm soát tốc độ
provider. Đợt chạy cuối tuần tự có 9/10 lượt HTTP 200. Không được bỏ mẫu 502
khỏi kết quả hay coi HTTP 200 là bằng chứng tư vấn đúng.

Một đợt bổ sung cho hai session chạy chồng nhau ghi nhận đồ ăn sáng mất 1,66 s;
chưa xác định nguyên nhân outlier này, không quy nó cho cold start. Cùng đợt đó,
luồng dầu gội → đổi người dùng → đổi dung tích → hỏi giá lần lượt mất
0,22 s / 4,72 s / 0,14 s / 0,02 s. Đổi người dùng vẫn giữ Composer/Reviewer vì
có thể thay đổi tính phù hợp hoặc an toàn, dù đã bỏ lượt Planner.

[Số liệu từng câu và tổng thời gian stage](artifacts/consultation_latency_optimization_20261117.json).

Phân tích stage cho luồng semantic cho thấy catalog chỉ khoảng 0,01–0,20 s.
Thời gian còn lại nằm ở các request tuần tự: Planner khoảng 0,7–3,1 s,
Composer khoảng 0,9–1,4 s và Reviewer khoảng 0,7–1,0 s. Một câu da dầu/mụn
còn thêm một lượt RetrievalRepair khoảng 0,7 s khi Planner sinh các loại hàng
không có trong catalog.

Mục tiêu dưới một giây đạt ở sáu mẫu tra cứu/lọc rõ ràng trong đợt tuần tự cuối,
chưa đạt cho mọi câu tư vấn tự do hoặc mọi điều kiện tải. Schema rút quá mạnh
đã làm tăng lỗi sinh JSON trong test live, nên được hoàn nguyên. Không mở rộng
fast path dựa riêng vào việc Planner trả `constraints=[]`: model có thể bỏ sót
nhu cầu đang nằm trong câu khách.

## Kiểm thử

```text
1044 passed in 34.68s
```

Full suite chạy với `CATALOG_REPOSITORY=demo` vì các fixture API/cart dùng SKU
demo; các test snapshot vẫn tạo repository snapshot riêng. Backend demo thực tế
giữ snapshot 3.081 hàng. Không đổi dữ liệu thật sang fixture để lấy kết quả pass.

## Nghiên cứu dùng để chọn kiến trúc

- [OpenAI — Latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization): giảm số request, giảm output và tái sử dụng kết nối là các đòn bẩy có tác động trực tiếp.
- [Groq — Optimizing latency](https://console.groq.com/docs/production-readiness/optimizing-latency): đo TTFT/độ dài output và giữ connection warm thay vì chỉ đổi model.
- [Adaptive-RAG, NAACL 2024](https://aclanthology.org/2024.naacl-long.389/): định tuyến theo độ phức tạp; bản này áp dụng nguyên tắc bằng fast path xác định và semantic path có kiểm tra, chưa đưa classifier học được vào sản phẩm.
- [So sánh kiến trúc của dự án](architecture_research_comparison_20261115.md): lý do giữ catalog làm nguồn sự thật và dùng model cho hiểu ý/diễn đạt.

## Việc chưa đạt

Planner vẫn có thể tự thêm các thuộc tính như hoạt chất, dạng gel hoặc sản phẩm
điều trị khi khách chỉ nói nhu cầu chung. Điều này có thể làm mất ứng viên,
thêm lượt repair hoặc trả hướng dẫn chung khi khách muốn một sản phẩm.
Reviewer đôi lúc vẫn chấp nhận suy diễn về độ dày thịt, số người dùng hoặc đặc
tính SKU; fallback có lúc chỉ liệt kê hàng chưa đủ sát nhu cầu. Đây là các lỗi
chất lượng còn tồn tại, chưa đạt mức nhân viên thực sự ở mọi câu.

Tốc độ semantic vẫn bị giới hạn bởi request tuần tự và sửa câu. Có 1 lỗi 502
trong 10 lượt cuối; chưa có trace stage của chính lượt đó để kết luận do rate
limit, render hay semantic review. Lượt profiling khác thành công không chứng
minh nguyên nhân của 502 này. Lần này không thay STT/VAD/TTS và không cam kết
tốc độ TTFT/audio.
