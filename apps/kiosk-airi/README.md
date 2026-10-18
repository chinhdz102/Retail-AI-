# SKY / RETAIL kiosk

Vue 3 + TypeScript one-screen kiosk for a multilingual retail assistant. The avatar is
rendered from the locally attributed `public/avatars/sky.glb` asset. Sky opens in a ready
state without adding a chat message or requesting microphone access. Visitors can type
in the text panel; voice capture starts only after they click the microphone button.
Sky supports Vietnamese, English and Chinese replies. Voice input uses the recorder and
Groq Whisper first; the browser's built-in Speech Recognition is not used for the main
transcription path. Selecting EN sends the English locale through transcription, the
retail agent and speech synthesis; the microphone controls and chat labels follow the
selected language as well.
Replies use Azure Speech Neural voices when the root `.env` has `AZURE_SPEECH_KEY` and
`AZURE_SPEECH_REGION`. Azure's F0 tier provides 500,000 Neural characters per month;
see the repository README for setup and quota details. When Azure is not configured, the
backend uses ElevenLabs if configured. Cloud audio streams from a short-lived, one-use URL.
If cloud synthesis fails, the app falls back to the browser's built-in speech synthesis.
If autoplay is blocked, click the audio-unlock prompt once.

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
