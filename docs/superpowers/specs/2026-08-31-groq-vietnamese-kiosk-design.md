# Groq Vietnamese Voice Kiosk - Design

**Ngày:** 2026-08-31  
**Trạng thái:** Approved by implementation request  
**Phạm vi:** Vertical slice kế tiếp của AI Retail Assistant

## Mục tiêu

Tạo một kiosk web chạy được với avatar `sky.glb`, cho phép khách nói tiếng Việt vào microphone, backend chuyển âm thanh thành văn bản bằng Groq Whisper, Qwen trả lời có grounding theo catalog demo, và kiosk đọc câu trả lời bằng giọng `vi-VN` trên máy local.

## Quyết định template/avatar

- Asset chính: `https://github.com/M3-org/avatars/blob/main/sky/sky.glb`.
- Asset được tải local vào `apps/kiosk-airi/public/avatars/sky.glb` để tránh phụ thuộc runtime vào raw GitHub.
- Repo `M3-org/avatars` không công bố license trong metadata; project giữ file attribution và chỉ coi asset là demo local cho tới khi quyền phân phối được xác nhận.
- Không copy nguyên monorepo AIRI. Kiosk giữ Vue/Vite để khớp backend mới và chỉ mượn ngôn ngữ bố cục stage + interactive area.

## Luồng voice

```text
Microphone
  → MediaRecorder (webm/opus)
  → POST /audio/transcribe
  → Groq whisper-large-v3-turbo, language=vi, retail vocabulary prompt
  → transcript shown in chat
  → POST /agent/respond
  → Groq OpenAI-compatible endpoint, qwen/qwen3.8-27b
  → grounded Vietnamese response
  → browser SpeechSynthesis (lang=vi-VN)
  → sky avatar speaking state
```

Groq account hiện có Qwen 3.8/3.6 và Whisper nhưng không có model TTS tiếng Việt. Vì vậy TTS browser là lựa chọn mặc định không cần thêm credential; giao diện có trạng thái lỗi nếu browser không có voice `vi-VN`.

## Backend boundaries

- `backend/config.py`: đọc `GROQ_API_KEY` bằng `SecretStr`, model/base URL/CORS/TTL.
- `backend/services/groq.py`: adapter duy nhất gọi Groq chat và transcription; API key không đi ra frontend.
- `backend/services/sessions.py`: session memory cô lập theo UUID cho demo local, có TTL và giới hạn context. Redis adapter vẫn là milestone hạ tầng kế tiếp.
- `backend/products/catalog.py`: catalog demo read-only để chứng minh grounding/product card; không đại diện database production.
- `backend/api/main.py`: CORS, health, sessions, products, agent và audio routes.

Nếu `GROQ_API_KEY` trống hoặc cả hai model Groq đều lỗi, agent trả lỗi provider an toàn và không gắn nhãn model giả cho câu trả lời. Adapter thử `GROQ_FALLBACK_MODEL` một lần; khi thành công, response ghi đúng model đã trả lời.

## Frontend boundaries

- `apps/kiosk-airi/src/App.vue`: orchestration session, chat, recorder, TTS và cart summary.
- `apps/kiosk-airi/src/components/SkyAvatar.vue`: chỉ render `<model-viewer>` và state visual, không chứa chat/network logic.
- `apps/kiosk-airi/src/components/ChatPanel.vue`: message list, composer, mic/send/stop controls và error/live regions.
- `apps/kiosk-airi/src/api.ts`: typed fetch calls tới FastAPI.
- `apps/kiosk-airi/src/styles.css`: token palette, responsive layout, reduced-motion, focus/contrast.

Avatar không tự động bật microphone khi mở trang. Customer phải nhấn nút mic vì browser permission và kiosk privacy.

## UI language

Đọc giao diện này như một AI-native retail kiosk: nền navy đậm, accent sky-blue đơn sắc, font sans rõ ràng, stage avatar lớn bên trái và interactive area bên phải. Motion chỉ dùng để biểu đạt listening/transcribing/thinking/speaking và feedback press; có `prefers-reduced-motion` fallback.

Tokens chính:

```text
background #070D1A
surface #101C2E
surface-elevated #172942
foreground #F4F8FC
muted #9DB0C7
accent #7DD3FC
accent-strong #38BDF8
danger #FB7185
success #86EFAC
radius 16px / 24px
```

## Error and privacy behavior

- Không có microphone permission: cho phép nhập text và hiển thị hướng dẫn bật quyền.
- STT thất bại/empty: không gửi agent, yêu cầu nói lại.
- Qwen lỗi: giữ transcript, hiển thị lỗi có nút thử lại; không trả claim giả.
- TTS không có voice tiếng Việt: vẫn hiển thị text, cho phép chọn voice browser khác.
- API key chỉ đọc ở backend `.env`; `.env` đã ignore và không được in log.
- Audio blob không lưu server sau request; session chỉ giữ tối đa các turn gần nhất.

## Verification targets

- Backend unit/integration tests cho session, catalog grounding, Groq adapter mapping và upload validation.
- Frontend Vitest tests cho message rendering, mic state transitions và API error state.
- `npm run build`, `npm run test:run`, `uv run pytest -q`, `uv run ruff check .` đều pass.
- Manual browser QA ở 375px, 768px, 1024px, 1440px; dark mode; reduced motion; keyboard focus.
