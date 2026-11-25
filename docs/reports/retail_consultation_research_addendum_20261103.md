# Bổ sung nghiên cứu tư vấn mua sắm — 03/11/2026

## Nguồn và giới hạn kiểm chứng

Đã đối chiếu toàn văn PDF 33 trang của [RealWorldShop, Findings EMNLP 2026](https://aclanthology.org/2026.findings-emnlp.740.pdf), gồm phụ lục về dữ liệu, điều khiển phiên, đánh giá và ca lỗi; [trang bài chính thức](https://aclanthology.org/2026.findings-emnlp.740/) và [bản arXiv](https://arxiv.org/abs/2609.38974). Bài dùng 3,28 triệu sản phẩm, khoảng 1.200 tình huống và hơn 2.000 hồ sơ tổng hợp. Điểm đáng áp dụng là quản lý trạng thái, chọn hành động, truy xuất có bằng chứng và chặn kết quả lỗi thời; không chỉ sửa lời nhắc. Tác giả báo cáo success 0,82 cho hệ đầy đủ, 0,71 khi gắn bộ điều khiển vào backbone chưa SFT/RL, 0,55 cho prompt-only (Bảng 3). Đây là kết quả mô phỏng, không phải chất lượng kiosk Việt.

Đánh giá có ba mức: từng lượt, tiến trình phiên, kết quả cuối. Phụ lục C.6 đối chiếu judge với ba người chấm trên 150 phiên và đo lặp lại; phần Limitations vẫn thừa nhận thiên lệch judge/simulator. Chưa tìm thấy URL phát hành chính thức cho kho SKU, bộ episode, checkpoint hay mã RealShop_Agent trong PDF, trang ACL/arXiv hoặc tìm kiếm ngày 03/11; không tuyên bố đã tải hay tái lập. Giấy phép bài báo không tự cấp quyền dùng dữ liệu nền tảng. Catalog là snapshot, không xác minh tồn kho hiện tại.

## Đối chiếu lỗi dự án và đề xuất triển khai

Các đề xuất dưới đây là thiết kế cho dự án, chưa được chạy hoặc nghiệm thu. Căn cứ thực nghiệm hiện có là [báo cáo chất lượng tư vấn](consultation_human_quality_20261103.md): lần cuối 8 lượt HTTP 200 nhưng 6 fallback, 2 hỏi thêm, không có hàng; do đó chưa có bằng chứng đạt mức nhân viên.

| Lỗi đang thấy | Thay đổi khả thi trên kiến trúc hiện có | Bằng chứng phải thu trước khi gọi là sửa xong |
|---|---|---|
| Trả lời chung chung, không đi tới sản phẩm | Sau khi có nhóm/chức năng đủ rõ, tìm trong catalog rồi đưa 2–3 lựa chọn hợp lệ kèm một khác biệt hữu ích. Không coi thiếu ngân sách/quy cách là luôn chặn việc giới thiệu. Với hướng dẫn chung, nêu tiêu chí chọn trước, hỏi một điều thực sự quyết định kết quả. | Hội thoại đầy đủ phải có ứng viên thật và lý do gắn nhu cầu; nếu không tìm được, nêu rõ khoảng trống. Đếm số lượt đủ thông tin nhưng không có tiến triển. |
| Hỏi vòng, hỏi lại thông tin đã có | Lưu câu hỏi đã hỏi và câu trả lời tương ứng; mỗi câu hỏi cần ghi trường còn thiếu và quyết định nào phụ thuộc vào nó. Nếu câu trả lời mới không thay đổi lựa chọn, bỏ câu hỏi. | Test khách đã nêu ngân sách, từ chối hàng, trả lời ngắn và yêu cầu chọn giúp; chấm hỏi trùng và số lượt tới lựa chọn hữu ích. |
| Đổi ngân sách/nhóm rồi vẫn dùng hàng cũ | Gắn ứng viên với phiên bản ràng buộc theo từng nhu cầu; cập nhật tiền/loại trừ/chức năng làm hết hiệu lực tập liên quan, nhưng không xóa nhu cầu khác. Lưu lý do loại hàng để tránh giới thiệu lại. | Chuỗi cập nhật–từ chối–chuyển nhóm–quay lại phải giữ đúng ràng buộc và không đưa ứng viên cũ chưa kiểm lại. |
| “Sẵn sàng” giả vì JSON hợp lệ, reviewer duyệt hoặc HTTP 200 | Tách trạng thái transport, có dịch vụ tư vấn, grounding, giữ ràng buộc và hoàn thành mục tiêu. Không đồng nhất `ready` nội bộ với lời khách chấp nhận hay nghiệm thu sản phẩm. | Báo riêng unavailable, hỏi hợp lý, gợi ý có hàng, khách quyết định và vi phạm nghiêm trọng; giữ toàn bộ mẫu số lượt khách yêu cầu phục vụ. |
| Reviewer duyệt lời khuyên sai hoặc sửa càng vòng | Kiểm tra ID/giá/đơn vị/tổng/loại trừ bằng dữ liệu và phép tính; reviewer chỉ bổ sung kiểm tra ngữ nghĩa có dẫn trường bằng chứng. Giới hạn một vòng sửa; bất đồng hoặc thiếu bằng chứng phải được thể hiện đúng. | Bộ lỗi có nhãn người chấm: reviewer bắt được bao nhiêu, bỏ sót bao nhiêu và sửa đúng thành sai bao nhiêu. So với không reviewer trên cùng draft và cùng ngân sách gọi mô hình. |

Không cài đáp án riêng cho lẩu, da mụn hay dọn bếp. Dùng chúng làm ca phát triển, sau đó kiểm tra trên nhu cầu mới. Với dị ứng, loại trừ hoặc yêu cầu an toàn, thiếu thuộc tính catalog có nghĩa **chưa xác minh được**; tuyệt đối không hạ yêu cầu thật của khách thành sở thích chỉ để tìm ra hàng. Không áp dụng máy móc cách làm mềm điều kiện trong bước sinh hồ sơ của benchmark vào runtime phục vụ khách.

## Reviewer không phải chứng nhận đúng

[Huang et al., Large Language Models Cannot Self-Correct Reasoning Yet, ICLR 2024](https://arxiv.org/abs/2310.01798), [toàn văn](https://arxiv.org/html/2310.01798v2), phân biệt tự sửa nội tại với phản hồi bên ngoài. Trên các bài suy luận được khảo sát, tự đánh giá bằng năng lực sẵn có không bảo đảm cải thiện, đôi khi làm giảm kết quả; so sánh cần tính cùng chi phí suy luận. Không suy rộng rằng mọi reviewer hoặc mọi mô hình mới đều vô ích. Áp dụng cho dự án: lời `accept` chỉ là phán đoán mô hình, còn chứng cứ SKU, phép tính và nhãn người chấm độc lập mới kiểm tra được phần tương ứng.

## Kế hoạch đánh giá văn bản, không cần dịch vụ mới

1. Giữ 35 câu/những journey hiện tại làm regression đã lộ; tạo bộ mù riêng từ các nhánh catalog khác, có đáp án/ràng buộc do người đọc kiểm trước. Một phiên gồm mục tiêu, thông tin được phép lộ từng lượt, cập nhật, từ chối và điều kiện thành công; agent không nhìn trường ẩn.
2. So cặp trên cùng snapshot/hash và cùng lượt người dùng: source hiện tại, thêm điều khiển tiến trình, rồi thêm kiểm tra bằng chứng. Chưa cần SFT/RL hay tải backbone 27B. Replay transcript và oracle catalog trước; chỉ đo hành vi mô hình thật khi có quota và ủy quyền phù hợp.
3. Chấm 0–2 riêng cho hiểu nhu cầu, lựa chọn/lý do, nhớ/cập nhật ràng buộc và nhịp hội thoại; ghi câu/field làm bằng chứng. Vi phạm loại trừ, bịa SKU/giá/hiệu quả hoặc lặp hàng bị bác là lỗi chặn, không bù bằng điểm diễn đạt. Khi khách cần hàng mà cả phiên không có lựa chọn, không cho N/A làm tăng điểm hoàn thành.
4. Báo tỷ lệ tới đề xuất hữu ích, tới quyết định, vòng hỏi thừa, bỏ cuộc, lỗi dịch vụ và độ trễ. Có thể báo thêm chất lượng trên phiên chạy thành công, nhưng luôn đặt cạnh tỷ lệ phục vụ trên toàn bộ phiên. Hai người chấm độc lập, ẩn tên cấu hình, phân xử bất đồng; judge mô hình chỉ hỗ trợ sàng lọc.

Tệp này chỉ bổ sung nghiên cứu. Không gọi provider, không sửa runtime và không thay kết luận chưa sẵn sàng của báo cáo thực nghiệm.
