# AI Retail Assistant

Grounded embodied AI retail assistant described in the V4 architecture plan.

## Current milestone

The vertical slice is in place:

- requirements and architecture contracts in `docs/`;
- FastAPI endpoints for sessions, catalog lookup, agent chat, Groq transcription and
  ElevenLabs synthesis;
- shared Pydantic wire enums/contracts;
- `uv` dependency manifest;
- PostgreSQL + pgvector and Redis Compose definition;
- One-screen Vue kiosk with local `sky.glb`, hands-free conversation, and selectable
  Vietnamese, English or Chinese speech/chat.

## Prerequisites

- Python 3.13 (the current workspace provides it through `py`/`uv`);
- `uv` 0.12 or newer;
- Docker Desktop for PostgreSQL/Redis integration tests (optional for unit tests).

## Setup on Windows PowerShell

```powershell
uv sync --all-groups
Copy-Item .env.example .env
# Open .env and set GROQ_API_KEY and ELEVENLABS_API_KEY to local secrets.
uv run pytest -q
uv run ruff check .
```

`GROQ_API_KEY` stays on the FastAPI process. Do not put it in `apps/kiosk-airi/.env`, a
`VITE_*` variable, or any browser extension bundle. The browser only calls the backend.
`ELEVENLABS_API_KEY` follows the same rule. Vietnamese is configured for
`Giang - Northern female Narrator` (`f5q6kePPoQAjCPYG6moa`); ElevenLabs Free plans
cannot synthesize that Professional voice through the API, so the backend automatically
uses the configured free Sarah voice for Vietnamese until the account is upgraded, then
the browser voice as a final fallback. All three languages use the low-latency ElevenLabs
Flash v2.5 model; English uses Sarah and Chinese uses River.

Run the kiosk in a second PowerShell window:

```powershell
npm --prefix apps/kiosk-airi install
npm --prefix apps/kiosk-airi run dev
```

Open `http://127.0.0.1:5173`. Sky greets the visitor, requests microphone access and
starts a continuous voice loop. Browsers with Speech Recognition use it directly;
other browsers use the microphone recorder with silence detection and Groq Whisper.
If the browser blocks autoplay, click \`Bật âm thanh\` once; the same gesture unlocks the
ElevenLabs audio element and resumes the pending reply. The supplied \`sky.glb\` includes
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

The architecture document defines the target system. The agent can use either the
in-memory demo catalog or the imported WinMart catalog in PostgreSQL. RAG,
provider-backed payment verification and Redis sessions remain roadmap items.

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
`shelf_source=SIMULATED`, while customer snapshots omit shelf values. Cart writes,
checkout and Redis-backed sessions remain separate roadmap items.
