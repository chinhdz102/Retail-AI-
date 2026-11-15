# Tiếp tục tối ưu tư vấn — 15/11/2026

## Kết luận

Đã commit phần tư vấn/catalog trước đó ở `b7feb6e` và tiếp tục sửa. **Chưa đạt mức nhân viên thực sự.** Trong đợt này đã gửi thêm 32 lượt text qua HTTP thật và một câu chẩn đoán gọi provider thật. Có 12/32 lượt HTTP 200, 20 lượt lỗi. Không coi HTTP 200 hoặc test hồi quy là tư vấn đạt.

Luồng shipping cuối trả đủ ba lượt nhưng còn lỗi nội dung: gọi gạo lức đỏ là cơm trắng, tự thêm nước mắm, đưa thời gian chế biến thiếu cơ sở và lặp phương án cũ. Các lỗi này được giữ lại nguyên văn trong artifact. Sau lượt đó bổ sung phân loại đồ sẵn trong bếp để không tự mua thêm; thay đổi này chưa có một bộ live hoàn chỉnh xác nhận.

Giữ nguyên catalog cũ `data/winmart_products.json`, không sửa lượt bán hoặc credentials. Đợt này chỉ kiểm tra tư vấn text, không chứng minh chất lượng STT/TTS/VAD.

## Các bản sửa tiếp theo

1. Thêm bộ lọc mua hàng bền qua phiên cho đồ **đã có/không mua**. LLM tạo thao tác có câu nguồn; code kiểm tra câu nguồn và tên hàng trong câu đó, gộp theo khóa ổn định rồi lọc cả kết quả tìm mới lẫn hàng nhớ từ lượt trước. Cho phép bỏ bộ lọc hoặc xóa khi đổi chủ đề. Phân biệt đồ không cần mua với đồ vẫn dùng trong bữa.
2. Lưu câu khách gần nhất đã xử lý. Chỉ khôi phục ngân sách từ những câu chưa được xử lý sau mốc đó, tránh lấy giá/ngân sách ở chủ đề cũ hoặc trước lúc khách hủy giới hạn. Vẫn hỗ trợ plan cũ chưa có trường mốc. Nhận thêm cách nói tăng/giảm ngân sách, không coi câu mô tả giá tăng/giảm là ngân sách.
3. `recommend` chọn tối đa một SKU; `compare` và giỏ món giữ mục đích riêng. `guide` không tự chọn hàng cần mua. Không bỏ các bước kiểm tra mã/giá/quy cách hoặc semantic review.
4. Câu hỏi làm rõ của planner trước đây trả trực tiếp, bỏ qua kiểm tra văn bản. Nay cũng phải qua kiểm tra ngôn ngữ nội bộ, số câu hỏi và dữ kiện; câu không hợp lệ không được gửi cho khách.
5. Mở rộng kiểm tra cách diễn đạt hợp tóc/da để không lách bằng “thích hợp”, “hợp”, “da đầu dầu”. Bắt lỗi dùng “dung tích” cho gram. Hướng dẫn lý do chọn phải có điểm cân nhắc thực tế, không chỉ nhắc giá.
6. Chỉ bỏ giá lặp trong lý do khi số đó đúng chính xác giá của dòng mua do code tính. Giá sai vẫn bị bác. Không xóa tùy tiện cả câu để che lỗi.
7. Hai lỗi định dạng khác nhau được sửa trong tối đa ba draft. Lỗi giống hệt lặp lại thì dừng; chỉ một lượt sửa lỗi semantic. Mọi draft cuối vẫn phải được review. Chưa có đo live riêng chứng minh tỷ lệ thành công tăng nhờ thay đổi này.
8. Không gửi lại toàn bộ plan cũ cho composer khi đã có plan hiện tại. Schema sinh JSON không yêu cầu model chép lại các trường state do code quản lý. Thêm mô tả rõ đơn vị cân và evidence. Chưa đo mức giảm token/độ trễ để công bố tỷ lệ cải thiện.
9. Thêm `Search.role=pantry` cho gạo/dầu/gia vị trong bữa ăn; loại các truy vấn này nếu khách không nhắc mua. Phân loại vẫn phụ thuộc planner, không phải bộ hiểu ngữ nghĩa hoàn chỉnh.
10. Sửa probe hội thoại để giữ câu khách và plan đã xác minh sau lỗi; dùng chung provider service qua các lượt để không mất cooldown. Thêm hai luồng kiểm tra phủ định/đồ đã có và chọn một dầu gội.

## Kết quả thật được giữ lại

| Artifact | Lượt | HTTP 200 | Lỗi | Phạm vi |
|---|---:|---:|---:|---|
| `advice_continue_before_20261115.json` | 5 | 4 | 1 | Câu 8, 9 và bữa tối 3 lượt |
| `advice_continue_after_20261115.json` | 17 | 2 | 15 | Câu 9 và long-dinner 16 lượt |
| `advice_continue_final_20261115.json` | 7 | 3 | 4 | Đồ đã có 4 lượt, dầu gội 3 lượt |
| `advice_continue_shipping_20261115.json` | 3 | 3 | 0 | Bữa tối, đã có gạo, chốt tổng |

Cả bốn artifact đều `complete=true` theo số lượt đã gửi. `complete` không có nghĩa đạt chất lượng. Chẩn đoán dầu gội nằm ở `advice_continue_shampoo_diagnostic_20261115.json`, tách khỏi số lượt HTTP để không đếm trùng phương thức.

Không thấy sai ID, giá thẻ hoặc tổng tiền trong 12 lượt HTTP 200 của các đợt trên. Chấm nội dung của trợ lý có 7 lượt đạt một phần, 25 lượt không đạt, gồm lỗi HTTP; không có lượt được dùng làm chứng cứ đã đạt mức nhân viên. Đây không phải đánh giá người dùng độc lập. Chi tiết: `artifacts/advice_continue_reviews_20261115.json`.

Các đợt chạy dùng các phiên bản mã khác nhau và mức quota khác nhau, không phải A/B kiểm soát. Log xác nhận cả 429/exhausted fallback và lỗi render/review, gồm thiếu số gram, lặp tên/giá và ngôn ngữ nội bộ. **Không quy toàn bộ 502 cho quota.** Khóa dự phòng có hoạt động nhưng không bảo đảm một lượt nhiều bước hoàn thành khi các vị trí khóa/model đều bị hạn mức.

## Kiểm chứng mã

379 test pass trong workspace hiện tại cho 8 file test tư vấn, review, provider, API, tìm hàng, snapshot và quy cách. Snapshot riêng của commit trước đó có 361 test pass; số khác nhau vì các test STT cũ được giữ trong commit tách riêng, còn workspace đang có các sửa STT chưa commit cùng phần tư vấn.

Các regression mới kiểm tra câu nguồn của bộ lọc mua, nhớ/hủy/đổi chủ đề, lọc hàng đã nhớ, không nhầm gạo với bánh gạo, khôi phục ngân sách sau lỗi planner, hủy ngân sách, chốt một lựa chọn, hướng dẫn không upsell, câu hỏi planner không lọt chữ nội bộ, g/volume, giá lặp đúng/sai và ngân sách sửa draft. Kết quả kiểm tra snapshot commit cuối được ghi trong `artifacts/advice_continue_validation_20261115.json`.

## Cơ sở nghiên cứu và giới hạn áp dụng

- [Diable — ACL Findings 2023](https://aclanthology.org/2023.findings-acl.615/): cập nhật state bằng thao tác trên bảng thay vì viết lại toàn bộ. Áp dụng vào set/remove và bộ lọc mua được code duy trì. Không tái tạo huấn luyện hay lấy điểm MultiWOZ làm điểm dự án.
- [Wizard of Shopping — ACL 2025](https://aclanthology.org/2025.acl-long.641/): hội thoại tìm sản phẩm bám kế hoạch và nhánh quyết định. Áp dụng vào việc tách chọn một loại, so sánh, giỏ mua và hướng dẫn; kiểm tra sửa ý trong cả phiên. Không coi hội thoại tổng hợp là chứng cứ đã đạt trải nghiệm nhân viên.

## Phần còn phải đạt

Chọn nguyên liệu và cách nấu còn thiếu chắc chắn; reviewer vẫn có thể bỏ lọt mô tả trái tên hàng và khẳng định khẩu phần. Một số câu chỉ đọc thông số thay vì giúp quyết định. Các ràng buộc mới còn phụ thuộc planner phân loại đúng; cần kiểm chứng thêm trên dữ liệu giữ kín và quota đủ. Bộ 35 câu gốc đã có kết quả trong báo cáo trước, chưa được chạy lại đầy đủ trên mã cuối của đợt này.

Không đánh dấu nghiệm thu hoàn tất. Việc cần tiếp theo là đánh giá có bằng chứng cho từng thuộc tính/cách dùng của hàng và giữ ý qua phiên, rồi chạy lại đủ 35 câu cùng luồng dài trên một phiên bản mã cố định.
