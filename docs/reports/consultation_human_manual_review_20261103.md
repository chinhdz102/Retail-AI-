# Tự rà soát nội dung tư vấn — 03/11/2026

**Chưa có bằng chứng đạt chất lượng nhân viên tư vấn.** Đọc thủ công toàn bộ 62 lượt:
54 lượt trước sửa và 8 lượt live cuối. Đây là tự rà soát của agent, không phải nhân viên
hoặc người dùng chấm mù. Không gọi thêm mô hình/dịch vụ mạng để chấm. Hai phiên bản có
hash riêng và không được gộp thành một điểm cải thiện.

| Tập bằng chứng | Không khả dụng | Hỏi nhận diện | Chỉ truy xuất | Nội dung tư vấn |
|---|---:|---:|---:|---|
| 54 lượt trước sửa | 36 fallback + 1 HTTP 502 | 10 | 6 | 1 giới hạn dữ liệu bán hàng; không có tư vấn hoàn tất |
| Riêng 35 câu người dùng | 22 fallback | 10 | 3 | Không có bằng chứng tư vấn hoàn tất |
| 8 lượt live cuối | 6 fallback | 0 | 0 | 2 lượt yếu, không có thẻ sản phẩm |

Fallback được tính là lỗi trải nghiệm/khả dụng, **không chấm ngữ nghĩa khi không có lời
tư vấn**. Hỏi nhận diện đúng trong phiên mới chỉ là bước đầu hợp lý. Trả đúng tên/giá/kệ
chỉ chứng minh phần truy xuất tương ứng; không tự trở thành thành công tư vấn.

## Điều thấy trực tiếp trong lời trả lời

- **Live cuối, lẩu lượt 1 — yếu:** hỏi số người có thể hữu ích để tính lượng, nhưng
  mở đầu lặp nhu cầu và chưa đưa hướng chuẩn bị; câu “để t tính” bắt chước đại từ viết
  tắt của khách, không tự nhiên với vai nhân viên.
- **Live cuối, lẩu lượt 2 — yếu:** khách hỏi loại thịt phù hợp, hệ thống chỉ hỏi “ưu tiên
  quy cách hay mức giá nào”. Chưa giải thích nên chọn thịt nào hoặc đánh đổi gì.
- **Live cuối, sáu lượt còn lại — không khả dụng:** lẩu sau khi khách nêu bò/ba chỉ và
  200 nghìn; cả ba lượt da mụn/da dầu; cả hai lượt dọn nhà. Không có lời tư vấn để chấm.
- **Trước sửa, lẩu — sai:** sau mục đích ăn lẩu và 200 nghìn, trả bò xay/viên và thịt
  399.000đ/kg, thiếu lượng mua phù hợp và lý do chọn. Đúng giá không chứng minh hợp món.
- **Trước sửa, đổi nước giặt → nước rửa chén dưới 50 nghìn — sai:** thẻ hàng có giá
  116.200đ, 115.100đ và 53.000đ; không lọc đúng trần mới.
- **Trước sửa, dọn bếp/lau nhà 150 nghìn — HTTP 502:** không có câu trả lời SSE.
  Sau sửa, live cuối nhận fallback hoàn chỉnh; đây là sửa luồng lỗi, chưa phải tư vấn tốt.
- **35 câu:** cả 10 câu nhận diện đều hợp lý trong phiên mới (#12/14/16/17/19/27/28/31/32/35).
  #9 đưa ba dầu gội dưới trần nhưng chưa giúp chọn. #26/29 trả mã kệ có giới hạn nguồn
  đúng, còn dài và nhiều mã. 22 câu còn lại đều fallback; không đánh dấu đạt vì HTTP 200.
- **Nhóm mới:** sổ ghi bài chỉ nhận câu “chưa tìm thấy” và hỏi lại loại hàng đã có;
  không chứng minh cửa hàng không bán sổ. Đồ uống bán chạy được từ chối xếp hạng khi
  thiếu số liệu đã xác minh; đó là giới hạn trung thực, chưa giúp quyết định mua.

## Rubric cần dùng khi đánh giá mức nhân viên

| Chiều đánh giá | Điều phải quan sát | Lỗi điển hình |
|---|---|---|
| Đúng nhu cầu | Món thực hiện đúng công việc khách cần | Sai chức năng, giỏ bữa ăn không hợp lý |
| Giúp quyết định | Có ưu tiên, lý do và đánh đổi liên quan | Chỉ liệt kê tên/giá hoặc đẩy việc chọn lại cho khách |
| Liên tục hội thoại | Giữ tham chiếu, câu trả lời, ràng buộc; hiểu đổi nhóm | Hỏi lại, mất tiêu chí, quay về tìm rộng |
| Có căn cứ | Tách hướng dẫn chung khỏi khẳng định từng SKU | Suy hiệu quả, mùi/vị, an toàn, tương thích từ tên/hãng |
| Ngân sách/đơn vị | Đúng trần, lượng, quy cách và tổng giỏ | Thẻ vượt giá, nhầm kg/gói/khẩu phần |
| Tiến triển | Mỗi lượt có bước hữu ích; chỉ hỏi điều thiết yếu | Xin phép lặp lại, hỏi bộ lọc chung, không trả lời ý chính |
| Tiếng Việt tự nhiên | Ngắn, rõ, phù hợp vai nhân viên | Lặp mở đầu, bắt chước tiếng lóng, dồn mã kỹ thuật |
| Khả dụng | Có lượt hoàn chỉnh và hữu ích | HTTP lỗi, fallback hoặc nhiều lượt không tiến triển |

Không có một tổng điểm “giống người” đã được xác thực. Hai lượt tư vấn thực sự ở bản
cuối đều yếu; chưa có lượt nào chứng minh hoàn tất quyết định mua ở mức nhân viên.
Các kiểm tra API “complete/cards_match_catalog/no_permission_loop” đều có thể qua trong
lúc câu trả lời yếu hoặc không có sản phẩm.

## Các nhóm khác còn thiếu bằng chứng live thành công

Cần kiểm tra tình huống mới về sổ bỏ túi/A5, pin remote AA/AAA, nồi dùng bếp từ, đèn
bàn hẹp, khăn lau kính ít xơ, đồ tắm chó con và hộp mang cơm/hâm nóng. Đây là các hướng
kiểm tra tiếp theo, không phải tuyên bố catalog có đủ từng loại. Không suy từ kiểm thử
giả lập ở các nhóm này ra khả năng tư vấn live.

Các nhóm đã thử nhưng vẫn thiếu thành công: hóa chất vệ sinh quanh mèo/mùi nhẹ,
đồ uống có nhãn đường và loại trừ caffeine, đồ ăn vặt theo tuổi, giỏ bữa tối/cả tuần,
chăm sóc da dầu/mụn và dầu gội theo nhu cầu. Cần đánh giá chọn giá trị, đổi ý, thay thế,
đổi loại hàng và giữ ràng buộc qua nhiều lượt; một lượt hỏi đúng chưa đủ.

Nguồn: [54 lượt trước sửa](artifacts/eval_subagent_full35_20261103.json),
[8 lượt live cuối](artifacts/consultation_human_final_live_20261103.json),
[62 nhận xét từng lượt, rubric và hash nguồn](artifacts/consultation_human_manual_review_20261103.json).
