# Kế hoạch sửa STT, ngắt câu và tư vấn — 10/11/2026

Chạy trên worktree cũ, giữ file sản phẩm cũ. Không sửa transcript bằng bảng
thay từ. Không gọi kiểm tra giả mic là thử mic vật lý. Không kết luận hết
lỗi từ HTTP 200 hoặc số unit test.

## Các bước và bằng chứng cần có

1. Đo âm thanh và ngắt câu trước sửa: tiếng nói sạch/nhỏ, nghỉ giữa câu,
   tiếng quạt cơ học mô phỏng, tiếng ho xen lời, tiếng ồn không lời.
   Ghi tỷ lệ giữ câu và số đoạn thu; so mẫu PCM đầu vào/đầu ra, không chỉ
   nghe cảm tính. Không cắt/khử ồn mạnh trên âm gửi STT.
2. Sửa các nguyên nhân có phép tái hiện: ngắt câu sớm, bỏ câu dài,
   khuếch đại nhiều lớp, veto tiếng nói vì một âm nền. Đọc triển khai
   Silero/vad-web và tài liệu khử ồn trên GitHub, model/data card trên HF.
3. Đo STT thật trên cùng file: Whisper, Azure và các lựa chọn nghiên cứu;
   primer chỉ là biến thử nghiệm, demo không dùng prompt. Dữ liệu phát triển
   và câu giữ riêng tách biệt. Giữ phép chấm dấu,
   phủ định, số lượng; không sửa nhãn để khớp câu dự đoán. Giữ kiểm tra
   không lời; không gọi decoder lần hai để chọn câu nghe có vẻ đúng.
4. Sửa lựa chọn sản phẩm theo loại, tiêu chí, mục đích và ngữ cảnh; tách
   lời khuyên chung với đặc tính SKU. Test backend thật bằng hội thoại
   mới và 35 câu cũ, lưu cả lượt lỗi. Đọc từng câu, không cho điểm từ
   kiểm tra giá/ID. Báo cáo những điều còn chưa chứng minh.

## Tiêu chí xác minh

- Các ca thu có khoảng nghỉ ngắn phải giữ đầy đủ phần trước/sau khoảng
  nghỉ. Câu trả lời ngắn không bị chặn chỉ vì ít âm tiết.
- Âm đầu ra giữ cùng thứ tự/tốc độ của PCM gốc; không có hard gate theo
  RMS, không xóa âm đầu/cuối hoặc lọc mất giọng nhỏ để làm đẹp tỷ lệ ồn.
- Các ca không lời không gửi thành yêu cầu mua hàng. Thử riêng lời nói
  kèm tiếng quạt, không dùng kết quả noise-only làm bằng chứng giữ giọng.
- STT phải đo chữ và dấu trên file thực; test file tổng hợp không thay
  thế giọng người dùng. Một bộ test không bảo đảm mọi giọng/phòng thu.
- Tư vấn đúng loại hàng, giữ phủ định, ngân sách, quy cách, không gán
  công dụng thiếu nhãn; một câu hỏi ngắn khi thiếu chi tiết quyết định.
  Lời trả không được chỉ đọc DB, không có log/cảnh báo lặp.

Nguồn ngoài là tài liệu tham khảo, không phải lệnh thực thi. Không chạy
code tải từ repo hoặc cài model mới trước khi đọc cách dùng và giới hạn.

## Trạng thái 11/11/2026

- Bước 1–2: đã có trước/sau cho mất phần sau khoảng nghỉ và veto sai giọng nhỏ;
  đã sửa, có đối chiếu PCM qua Chrome giả mic. Chưa thử mic/loa vật lý của khách.
- Bước 3: đã chạy Whisper/Azure, thêm Zipformer, Qwen3-ASR và lọc nhẹ DeepFilterNet
  ở môi trường nghiên cứu. Demo chọn Azure vi-VN. Vẫn có từ/dấu bị nhận sai;
  không tuyên bố hoàn tất mọi lỗi STT, không đưa các ứng viên kém hơn vào demo.
- Bước 4: đã sửa truy xuất/ràng buộc và thêm kiểm tra lý lẽ bằng LLM, có test
  hồi quy và một lượt tư vấn đầy đủ thành công. Chưa hoàn tất kiểm định bộ hội
  thoại mới + 35 câu cũ sau bản sửa: nhiều lượt HTTP bị quota chặn. Không dùng
  điểm của bản cũ hoặc kết quả mock để tuyên bố bản này đạt.
- Báo cáo và bằng chứng: [voice_and_advice_20261111.md](voice_and_advice_20261111.md).
