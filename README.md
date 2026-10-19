# AI Retail Assistant

Grounded embodied AI retail assistant described in the V4 architecture plan.

## Current milestone

The vertical slice is in place:

- requirements and architecture contracts in `docs/`;
- FastAPI endpoints for sessions, catalog lookup, agent chat, Groq transcription and
  Azure Speech / ElevenLabs synthesis;
- shared Pydantic wire enums/contracts;
- `uv` dependency manifest;
- PostgreSQL + pgvector and Redis Compose definition;
- One-screen Vue kiosk with local `sky.glb`, hands-free conversation, and selectable
  Vietnamese, English or Chinese speech/chat.

English is supported across the chat interface, Groq Whisper transcription, catalog
intent routing and speech synthesis. English category and product terms such as `milk`,
`coffee`, `snacks` and `instant noodles` are mapped to the Vietnamese WinMart taxonomy
when needed; product facts still come from the active catalog. Prices are in VND. The
catalog does not contain loyalty rules, return policies, store hours, sales rankings,
taste ratings or interaction analytics. Sky states that those facts are unverified and
suggests asking store staff instead of guessing.

## Prerequisites

- Python 3.13 (the current workspace provides it through `py`/`uv`);
- `uv` 0.12 or newer;
- Docker Desktop for PostgreSQL/Redis integration tests (optional for unit tests).

## Setup on Windows PowerShell

```powershell
uv sync --all-groups
Copy-Item .env.example .env
# Open .env and set GROQ_API_KEY. Azure Speech is optional but recommended for free cloud TTS.
uv run pytest -q
uv run ruff check .
```

`GROQ_API_KEY` stays on the FastAPI process. Do not put it in `apps/kiosk-airi/.env`, a
`VITE_*` variable, or any browser extension bundle. The browser only calls the backend.
TTS uses Azure Speech F0 when `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION` are configured.
The F0 tier includes 500,000 Neural characters per month; its real-time TTS quota is 20
requests per 60 seconds. Azure supplies Vietnamese `vi-VN-HoaiMyNeural`, English
`en-US-JennyNeural`, and Mandarin `zh-CN-XiaoxiaoNeural` voices. See Microsoft's
[Speech pricing](https://azure.microsoft.com/en-us/pricing/details/speech/),
[TTS quotas](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-services-quotas-and-limits),
and [voice list](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts).
Create an Azure Speech resource on the F0 tier and copy its key and region into the root
`.env`. Azure is preferred when configured. Without Azure credentials, the API uses
ElevenLabs if configured; if cloud synthesis is unavailable, the kiosk falls back to the
browser's built-in speech synthesis. Azure failures go directly to the browser fallback so
the app does not silently switch to a potentially billable provider.

Run the kiosk in a second PowerShell window:

```powershell
npm --prefix apps/kiosk-airi install
npm --prefix apps/kiosk-airi run dev
```

Open `http://127.0.0.1:5173`. Sky waits in a ready state without adding a chat bubble or
requesting microphone access. Open the text panel to type a question or select a quick
prompt. Voice capture starts only after the visitor clicks the microphone button.
The microphone recorder sends captured speech to Groq Whisper (`whisper-large-v3-turbo`,
then `whisper-large-v3` on provider failure). Local adaptive voice detection trims silence
and very short noise before upload. Cloud TTS streams through a short-lived, one-use URL;
see [the voice pipeline notes](docs/voice-pipeline.md).
If the browser blocks autoplay, click \`Bật âm thanh\` once; the same gesture unlocks the
audio element and resumes the pending reply. The supplied \`sky.glb\` includes
its own \`talk\` animation, which the kiosk uses directly while Sky is speaking. If the API
runs on another origin, copy
`apps/kiosk-airi/.env.example` to `apps/kiosk-airi/.env` and set `VITE_API_BASE_URL`.

Start infrastructure when Docker Desktop is installed:

```powershell
docker compose up -d
docker compose ps
```

Run the API locally:

```powershell
uv run uvicorn backend.api.main:app --reload
```

Then open `http://127.0.0.1:8000/health` or `http://127.0.0.1:8000/docs`.

## Contracts

- [Requirements](docs/requirements.md)
- [Architecture](docs/architecture.md)
- [Tool contracts](docs/tool_contracts.md)
- [Database ERD](docs/database_erd.md)
- [Implementation plan](docs/superpowers/plans/2026-08-31-architecture-contract-and-bootstrap.md)
- [Groq + Vietnamese kiosk plan](docs/superpowers/plans/2026-08-31-groq-vietnamese-kiosk.md)

The architecture document defines the target system. Current runtime status and its
limits are recorded in [`docs/reports/retail-ai-optimization-report.md`](docs/reports/retail-ai-optimization-report.md).
The WinMart PostgreSQL catalog is read-only for customer lookups. Development sessions use
isolated in-memory state; production selects the Redis session adapter and fails closed if
Redis is unavailable. Cart and checkout are explicitly sandbox-only, use the demo catalog,
and are not connected to a POS, persistent order database or payment provider.

## Agent tool loop

`POST /agent/respond` exposes read-only `search_products`, `recommend_products` and
`get_product_snapshot` tools while a session is browsing. Search can resolve unavailable
items; recommendation always filters out zero-stock products and applies structured price
operators. Fact lookups and recommendations return deterministic answers after one tool
call. Other searches can continue through the two-round, five-call agent loop. The response
includes `tools_used` and `grounded_product_ids` for inspection.

The demo catalog remains the default. To validate the tracked WinMart dataset without
writing to the database, run:

```powershell
uv run python scripts/import_winmart.py --report evaluation/results/winmart_catalog_import_dry_run.json
```

To create the PostgreSQL catalog tables and import accepted rows, start Docker Compose,
run the importer with `--apply`, then set `CATALOG_REPOSITORY=postgres` in `.env` and
restart the API:

```powershell
docker compose up -d postgres
uv run python scripts/import_winmart.py --apply
```

The importer hashes the source file for dataset versioning, validates identifiers and
prices, and quarantines rows whose sale price exceeds their regular price. The dataset's
shelf locations are crawler-generated placeholders; PostgreSQL preserves them with
`shelf_source=SIMULATED`, while customer snapshots omit shelf values. Demo shopping actions
are available only with `CATALOG_REPOSITORY=demo`; the service rejects transaction writes
against the imported WinMart snapshot because it is not branch-authoritative inventory.

Branch profiles, active promotion campaigns, receiving events, and nearby-place snapshots
use separate source tables and freshness checks. The WinMart catalog importer does not populate
those operational feeds. Until the store master maps each kiosk to a real branch and the feeds
are loaded, the assistant will say the information is unavailable. See
[`docs/retail-information-data-design.md`](docs/retail-information-data-design.md) for the data
contract and source-coverage status.
