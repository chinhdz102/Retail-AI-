# Groq Vietnamese Voice Kiosk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây vertical slice kiosk Vue có avatar `sky.glb`, hội thoại Qwen qua Groq, STT tiếng Việt bằng Whisper và TTS `vi-VN` trên trình duyệt.

**Architecture:** FastAPI giữ API key và gọi Groq qua OpenAI-compatible client. Session/catalog demo là service read-only, còn frontend Vue/Vite chỉ gọi backend và điều khiển microphone/TTS. Avatar được render bằng `@google/model-viewer`; UI giữ cấu trúc AIRI stage + interactive area nhưng không copy monorepo AIRI.

**Tech Stack:** FastAPI, Pydantic Settings, OpenAI Python client, Groq Qwen 3.8/3.6, Whisper large v3 turbo, Vue 3, Vite, TypeScript, `@google/model-viewer`, `@phosphor-icons/vue`, Vitest, Playwright browser QA.

## Global Constraints

- API key chỉ tồn tại ở backend `.env`; frontend chỉ biết `VITE_API_BASE_URL`.
- Chat model mặc định `qwen/qwen3.8-27b`, fallback `qwen/qwen3.6-27b`; STT mặc định `whisper-large-v3-turbo`, `language=vi`.
- TTS mặc định dùng browser `SpeechSynthesis` với `lang=vi-VN`; không giả định Groq có Vietnamese TTS.
- `sky.glb` tải local để demo; repo nguồn không khai báo license, phải giữ attribution/cảnh báo quyền phân phối.
- Mọi session phải cô lập theo UUID; không lưu raw audio server-side sau request; LLM không là business truth.
- UI phải có keyboard focus, touch target tối thiểu 44px, dark/light token parity, reduced-motion và responsive fallback.

---

### Task 1: Backend configuration and Groq adapters

**Files:**
- Create: `backend/config.py`
- Create: `backend/services/__init__.py`
- Create: `backend/services/groq.py`
- Create: `tests/test_groq_services.py`
- Modify: `backend/api/main.py`
- Modify: `pyproject.toml`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `GROQ_API_KEY`, `GROQ_BASE_URL`, `GROQ_MODEL`, `GROQ_FALLBACK_MODEL`, `GROQ_STT_MODEL`.
- Produces: `get_settings()`, `GroqService.chat(messages, model=None) -> str`, `GroqService.transcribe(filename, content, content_type) -> str`.

- [ ] **Step 1: Add failing adapter tests**

Test a fake OpenAI-compatible client returning a Qwen message and a transcription; assert model IDs and `language="vi"` are passed. Test missing key returns a safe `GroqConfigurationError` without exposing secret text.

- [ ] **Step 2: Run tests to verify RED**

Run: `uv run pytest tests/test_groq_services.py -q`  
Expected: FAIL because config/service modules do not exist.

- [ ] **Step 3: Implement settings and adapters**

Use `SecretStr` for the key and instantiate `AsyncOpenAI(api_key=settings.groq_api_key.get_secret_value(), base_url=settings.groq_base_url)`. Catch provider errors, try fallback chat model once, and raise a safe typed error if both fail. Pass `language="vi"` and a retail prompt to audio transcription.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `uv run pytest tests/test_groq_services.py -q`  
Expected: all adapter tests pass.

### Task 2: Demo sessions and grounded catalog API

**Files:**
- Create: `backend/services/sessions.py`
- Create: `backend/products/__init__.py`
- Create: `backend/products/catalog.py`
- Create: `tests/test_retail_api.py`
- Modify: `backend/api/main.py`

**Interfaces:**
- Consumes: existing shared contracts and `GroqService`.
- Produces: `POST /sessions`, `POST /sessions/{id}/close`, `POST /agent/respond`, `GET /products/search`, `GET /products/{id}/snapshot`, `POST /audio/transcribe`.

- [ ] **Step 1: Add failing endpoint tests**

Cover session isolation, unknown product `404`, search by Vietnamese query, agent request validation and audio content type rejection. Override Groq service with a fake in tests so no real API call is made.

- [ ] **Step 2: Run tests to verify RED**

Run: `uv run pytest tests/test_retail_api.py -q`  
Expected: FAIL because routes/store/catalog do not exist.

- [ ] **Step 3: Implement minimal services/routes**

Use a bounded in-memory store with `session_id -> {state, messages, created_at, expires_at}` and keep only the latest 8 messages. Build a Vietnamese system prompt that says catalog facts come from `retail_context`; inject deterministic snapshot JSON before calling Qwen. Never concatenate secrets or raw SQL into the prompt.

- [ ] **Step 4: Run endpoint tests GREEN**

Run: `uv run pytest tests/test_retail_api.py -q`  
Expected: all retail endpoint tests pass.

### Task 3: Frontend scaffold and asset attribution

**Files:**
- Create: `apps/kiosk-airi/` using Vite Vue TypeScript template.
- Create: `apps/kiosk-airi/public/avatars/ATTRIBUTION.md`
- Create: `apps/kiosk-airi/public/avatars/sky.glb` (downloaded source asset)
- Create: `apps/kiosk-airi/public/avatars/sky-poster.png` (downloaded source preview)
- Modify: root `README.md`

**Interfaces:**
- Consumes: M3-org asset URL and backend `VITE_API_BASE_URL`.
- Produces: `npm run dev`, `npm run build`, `npm run test:run`.

- [ ] **Step 1: Scaffold and install dependencies**

Run `npm create vite@latest apps/kiosk-airi -- --template vue-ts`, then install `@google/model-viewer`, `@phosphor-icons/vue`, `vitest`, `@vue/test-utils`, `jsdom`, and `happy-dom` as needed. Do not add React/Next dependencies.

- [ ] **Step 2: Download and document asset**

Download `sky.glb` and `glb_sky.png` from the exact M3-org URLs. Write source URL, SHA-256, retrieval date and license-unknown warning to `ATTRIBUTION.md`.

- [ ] **Step 3: Add frontend test harness**

Configure Vitest with jsdom and a `test:run` script. Add a smoke test that mounts the app and finds the labelled microphone button.

### Task 4: AIRI-inspired kiosk UI and voice interaction (TDD)

**Files:**
- Create: `apps/kiosk-airi/src/api.ts`
- Create: `apps/kiosk-airi/src/types.ts`
- Create: `apps/kiosk-airi/src/components/SkyAvatar.vue`
- Create: `apps/kiosk-airi/src/components/ChatPanel.vue`
- Modify: `apps/kiosk-airi/src/App.vue`
- Modify: `apps/kiosk-airi/src/main.ts`
- Modify: `apps/kiosk-airi/src/style.css`
- Create: `apps/kiosk-airi/src/App.test.ts`

**Interfaces:**
- `createSession(kioskId) -> Promise<SessionResponse>`
- `respond(sessionId, text) -> Promise<AgentResponse>`
- `transcribe(sessionId, audioBlob) -> Promise<TranscriptionResponse>`
- `SkyAvatar` props: `{ src: string; state: "idle" | "listening" | "thinking" | "speaking" | "error" }`
- `ChatPanel` props/events: messages, status, `send(text)`, `toggleRecording()`, `stopSpeaking()`.

- [ ] **Step 1: Write failing UI tests**

Assert labelled mic/send controls exist, empty state shows a Vietnamese prompt, clicking a quick prompt adds a user message, and an API error renders an inline retry action.

- [ ] **Step 2: Run frontend tests RED**

Run: `npm --prefix apps/kiosk-airi run test:run`  
Expected: FAIL because kiosk components are not implemented.

- [ ] **Step 3: Implement shell and Sky avatar**

Use a full-height CSS grid: stage area with `<model-viewer src="/avatars/sky.glb">` on the left, chat/interactive area on the right, and a compact cart strip below. Use Phosphor icons only, semantic buttons, `aria-live="polite"`, labelled inputs, focus rings and 44px touch targets.

- [ ] **Step 4: Implement recorder and Vietnamese TTS**

Use `navigator.mediaDevices.getUserMedia({ audio: true })`, `MediaRecorder`, `audio/webm;codecs=opus` when supported, then call `transcribe`. Use `SpeechSynthesisUtterance` with `lang="vi-VN"`, choose the first Vietnamese voice, and map avatar state to `listening/transcribing/thinking/speaking`.

- [ ] **Step 5: Run frontend tests GREEN**

Run: `npm --prefix apps/kiosk-airi run test:run`  
Expected: all UI tests pass.

### Task 5: Integrated verification and browser QA

**Files:**
- Modify: `apps/kiosk-airi/.env.example`
- Modify: `README.md`
- Create: `apps/kiosk-airi/vite.config.ts`

**Interfaces:**
- Consumes: backend API and frontend build artifacts.
- Produces: documented local commands and a manually verified kiosk flow.

- [ ] **Step 1: Run backend verification**

Run: `uv run pytest -q` and `uv run ruff check .`  
Expected: all Python tests pass and Ruff exits 0.

- [ ] **Step 2: Run frontend verification**

Run: `npm --prefix apps/kiosk-airi run test:run` and `npm --prefix apps/kiosk-airi run build`  
Expected: Vitest and Vite build exit 0.

- [ ] **Step 3: Verify browser flow**

Run backend with `uv run uvicorn backend.api.main:app --reload`, frontend with `npm --prefix apps/kiosk-airi run dev`, then verify session creation, text chat, mic permission, Vietnamese transcript, Qwen response and `vi-VN` speech. Verify 375px and desktop layouts, keyboard focus, dark mode and reduced motion.

- [ ] **Step 4: Record infrastructure limits**

Docker/PostgreSQL/Redis remain optional for this vertical slice if Docker is unavailable. The in-memory session store is explicitly a demo fallback and must be replaced by Redis before production or multi-instance deployment.
