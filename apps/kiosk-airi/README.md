# SKY / RETAIL kiosk

Vue 3 + TypeScript one-screen kiosk for a multilingual retail assistant. The avatar is
rendered from the locally attributed `public/avatars/sky.glb` asset. Sky greets visitors,
listens continuously when supported, and speaks replies in Vietnamese, English or Chinese.
Replies are synthesized by the backend with ElevenLabs Flash v2.5. On a Free ElevenLabs
plan, Vietnamese automatically uses the Sarah premade voice because Giang requires a paid
plan. If autoplay is blocked, click the audio-unlock prompt once.

## Run

```powershell
npm install
npm run dev
```

The API base URL defaults to `http://127.0.0.1:8000`. To override it, copy
`.env.example` to `.env` and set `VITE_API_BASE_URL`.

The Groq credential is intentionally not a frontend variable. Configure it in the
repository root `.env` used by FastAPI; never use `VITE_GROQ_API_KEY`.

## Verify

```powershell
npm run test:run
npm run build
```
