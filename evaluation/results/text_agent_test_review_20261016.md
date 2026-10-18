# Text-agent test review - 2026-10-16

## Scope

Text conversations only; STT and TTS were left out as requested. Live model checks used the configured Groq models and the local API. This is a coverage probe, not a statistical estimate of hallucination frequency.

## Results

| Check | Result |
| --- | ---: |
| Backend tests (`pytest`) | 102 passed |
| Frontend tests (Vitest) | 23 passed |
| Frontend production build | Passed; Vite warns that the main JavaScript chunk is 1.16 MB |
| Deterministic feature smoke benchmark | 23 pass, 0 fail, 7 unsupported |
| Scripted safety regressions | 40/40 across 8 cases repeated 5 times |
| Full live Groq suite before the final inventory-guard fix | 25 turns across 23 scenarios; 25 HTTP 200; 24 automated passes, 1 failure |
| Live rerun of the failed inventory case after its fix | 1/1 passed; 1 model call, no catalog tool call |
| Exact screenshot prompt through the running HTTP API | HTTP 200; grounded Coca Zero price from catalog tools |
| Frontend-to-API CORS preflight | HTTP 200; `http://127.0.0.1:5173` allowed |

The full live suite's only remaining failure was the owner request to increase stock. The agent did not change inventory, but it answered that the product was not found. The inventory action detector now catches increase/decrease verbs and returns the safe refusal. That exact case was rerun live and passed. The other 24 turns passed in the full run; the full suite was not rerun after this narrowly scoped guard change.

## Live scenarios covered

The live suite exercised price, shelf, stock, constrained recommendations, ingredients, spiciness, missing promotion data, expired stock, unknown products, unavailable nutrition facts, price/inventory changes, payment claims, reservations, cart and checkout requests, staff handoff, English and Chinese price questions, and multi-turn product references.

The exact screenshot prompt, "the Coca Zero price is how much?", was also sent to the running HTTP API in Vietnamese. It returned `13.000 VND`, used `search_products` and `get_product_snapshot`, and grounded the answer to `COKE_ZERO_330`. In live tests, Qwen had provider errors on recommendation prompts; the configured `openai/gpt-oss-120b` fallback completed them successfully. The `openai/gpt-oss-20b` fallback was not observed.

## Fixes made during testing

- Vietnamese normalization now maps the Vietnamese cross-bar d to `d`, and the shelf detector matches location phrases without treating the verb "tell a story" as a shelf request.
- When the model skips catalog tools for a supported fact question, the API extracts the product query, searches through the existing tool gateway, and returns a fact only when one product is identified. Tests cover the no-tool path for milk shelf and water stock questions.
- Inventory write intents such as "increase stock" are detected and refused before a catalog lookup. No owner write tools exist.
- The kiosk no longer inserts a chat greeting when the session starts. The empty conversation state remains until the user sends text. A frontend test checks that startup creates no chat message and does not call the agent.
- Benchmark assertions accept Vietnamese VND formatting and equivalent safe inventory-refusal wording.

## Data and feature limits

The active runtime repository is `demo`, with 6 products. The WinMart data file contains 3,099 rows: 3,081 accepted and 18 quarantined during validation. Live search against PostgreSQL was not tested, so these Groq results do not demonstrate coverage of all accepted rows.

The smoke benchmark marks these features unsupported: FEFO, cart writes, knowledge-base lookup, cross-turn cart references, payment verification, Redis-backed sessions, and RAG. A separate owner-management agent is also not implemented; price and inventory changes are refused safely.

## Artifacts

- `live_text_final_20261016.json` - 25-turn full live run before the final inventory-guard fix.
- `live_text_owner_fix_20261016.json` - live rerun of the fixed inventory refusal.
- `live_text_followup_20261016.json` - six earlier live follow-up cases for the screenshot query, natural shelf/stock questions, inventory refusal, and English/Chinese prices.
- `live_text_repro_20261016.json` - initial reproduction of the screenshot price question.
- `smoke_benchmark_final_20261016.json` - latest deterministic coverage and unsupported features.
- `p0_regression_5x.json` - repeated scripted safety regressions.
