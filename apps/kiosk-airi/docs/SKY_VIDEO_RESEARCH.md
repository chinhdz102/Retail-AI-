# Nghiên cứu avatar video Mai

## Kết luận nhanh

Với kiosk bán lẻ, avatar không cần lip-sync chính xác theo từng âm vị. Phương án ổn định và nhẹ hơn là dùng video image-to-video ngắn cho từng trạng thái, tắt audio của video, rồi phát TTS riêng từ app.

Hiện đã có **4 clip local** dùng được cho demo:

- `sky-clip-01-idle.mp4`
- `sky-clip-02-listening.mp4`
- `sky-clip-03-thinking.mp4`
- `sky-clip-04-speaking.mp4`

Muse.ai đã nhận ảnh reference nhưng trả lỗi video generation unavailable for the current account region, nên **số clip tạo trực tiếp trong Muse.ai hiện là 0**. Bốn clip local là fallback để app chạy được ngay. Khi Muse cho phép render, chỉ cần thay bốn file bằng file cùng tên.

## Điều các hướng dẫn và paper thống nhất

1. Ảnh reference giữ ngoại hình. Prompt video nên tập trung vào chuyển động, camera và nhịp thay vì mô tả lại toàn bộ nhân vật. Điều này được nêu trong [Runway Image to Video Prompting Guide](https://help.runwayml.com/hc/en-us/articles/48324313115155-Image-to-Video-Prompting-Guide), [Google video best practices](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/best-practice?hl=en) và [Sora prompting guide](https://developers.openai.com/cookbook/examples/sora/sora2_prompting_guide).
2. Muốn giữ nhân vật ổn định nên dùng cùng ảnh reference, cùng khung hình, cùng nền, cùng quần áo và camera cố định cho mọi clip. [Animate Anyone](https://openaccess.thecvf.com/content/CVPR2024/papers/Hu_Animate_Anyone_Consistent_and_Controllable_Image-to-Video_Synthesis_for_Character_Animation_CVPR_2024_paper.pdf) cũng xem consistency theo thời gian là thách thức chính của image-to-video.
3. Lip-sync thật là một bài toán khác. Các hướng như [StyleTalker](https://arxiv.org/abs/2208.10922) nhận audio làm điều kiện để tạo khẩu hình. Cách đó phù hợp khi cần khớp âm vị, nhưng không cần thiết cho avatar kiosk nói câu thay đổi liên tục và ưu tiên tải nhanh.

## Pipeline nên dùng cho Mai

### 1. Khóa reference

Giữ một ảnh chính diện 16:9 với một bộ đồng phục duy nhất. Không tạo lại nhân vật từ prompt chữ cho từng clip. Dùng `sky-anime-reference.png` làm ảnh gốc và giữ các chi tiết sau trong mọi prompt:

- tóc bob teal sẫm
- kẹp tóc sao san hô
- áo khoác dài tay teal, thân áo kem, áo trong teal
- huy hiệu sao san hô bên ngực trái
- nền gradient navy teal
- camera chính diện cố định

### 2. Chia theo trạng thái

- `idle` — chớp mắt và thở nhẹ
- `listening` — nhìn camera, gật nhẹ, miệng đóng
- `thinking` — nhìn hơi lên, chớp mắt, miệng đóng
- `speaking` — khẩu hình nhẹ, chớp mắt, gật rất nhỏ

Mỗi clip 4–6 giây và lặp vô hạn. Không đưa câu trả lời cụ thể vào video vì nội dung TTS thay đổi theo mỗi truy vấn.

### 3. Xuất file nhẹ

- H.264 MP4
- 1280×720 hoặc 854×480 nếu thiết bị yếu
- 24 hoặc 30 fps
- không audio
- `faststart` để phát sớm khi tải qua HTTP
- giữ mỗi clip khoảng vài trăm KB đến vài MB

### 4. Nối vào app

`SkyVideoAvatar.vue` chỉ giữ một thẻ `<video>`, tải clip đang cần, đặt `muted`, `playsinline`, `loop`, `preload="metadata"` và dùng poster khi clip lỗi. Trạng thái TTS trong `App.vue` điều khiển cùng state video. Cách này tránh tải cả bốn clip cùng lúc và không tạo hai nguồn âm thanh.

Bản fallback hiện tại đã được làm lại bằng các frame trạng thái riêng cho blink, listening và thinking, cùng frame miệng nói. Đây là motion preview local, không phải kết quả render native của Muse.ai.

## Prompt mẫu cho Muse

Dùng cùng ảnh reference cho mọi lần render. Chỉ thay đoạn chuyển động:

```text
Use the attached Mai image as the only character reference. Preserve the exact same face,
hair, single teal-and-cream uniform, coral star hair clip, coral chest badge, navy-teal
gradient background, lighting, crop, and fixed front camera. No new clothes, props, text,
logo, watermark, extra people, camera cuts, or audio. Generate a subtle 5-second 16:9
image-to-video loop for a retail kiosk. Motion only: [STATE MOTION]. Keep the mouth closed
except for the speaking state. Export a silent H.264 MP4.
```

## QA trước khi thay file

Kiểm tra từng clip ở đầu, giữa và cuối clip. Nếu thấy đổi áo, đổi màu tóc, thêm chữ, đổi nền, rung camera hoặc biến dạng tay thì bỏ clip đó và render lại. Không cần đánh giá khẩu hình theo từng từ trong bản demo này.
