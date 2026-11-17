# Consultation and browse latency — 2026-11-17

## Scope

This pass checked the live text endpoint against the active WinMart snapshot and
the same catalog-backed consultation path used by the kiosk.  The test set
covers greetings, breakfast/night food, laundry, hair care and an acne-related
request.  The catalog remains the source of product identity, price, stock and
category; the LLM is used for turns that contain real preferences or safety
constraints.

## Changes

- Added a taxonomy-driven occasion plan.  “Ăn sáng” searches catalog rows under
  the catalog's breakfast taxonomy; “ăn đêm” searches the catalog's ready-meal
  types (mì/cháo/miến), without naming a SKU in code.
- Added a deterministic response for an unambiguous browse turn.  It still
  applies catalog filtering, stock, price and choice-count guards, then renders
  a short Vietnamese employee-style introduction with the verified product
  cards.  It does not run for budgets, exclusions, follow-up state, comparisons
  or subjective/safety constraints.
- Added planner outage recovery and stopped retrying a second remote composition
  after a mechanical label/shelf-claim rejection.  Provider keys/models still
  fall back in the configured order; all-key cooldowns go directly to the
  verified catalog fallback.

## Live timings

Measured with `POST /agent/respond`, a fresh session per turn, local HTTP on
`127.0.0.1:8000`, snapshot catalog:

| Turn | Result | Wall time |
|---|---|---:|
| `thế có đồ gì ăn sáng ko` | deterministic catalog browse | 0.380 s |
| `có gì ăn đêm không` | deterministic catalog browse | 0.281 s |
| `Mình muốn xem vài loại nước giặt để chọn.` | deterministic catalog browse | 0.615 s |
| `Mặt mình da dầu, đang nhiều mụn...` | LLM/recovery path, provider rate-limit | 9.913 s |

The first three clear browse turns meet the sub-second target.  The last turn
intentionally stays on the semantic LLM path: reducing it to a blind lexical
lookup would risk turning “da dầu/nhiều mụn” into the wrong product type.  Its
latency is dominated by sequential provider key/model fallback and remote
review, so it is not claimed to meet the sub-second target yet.

## Verification

- Backend: **975 passed**.
- Frontend: **66 passed / 8 files**.
- Frontend production build: passed.
- Smoke HTTP responses for breakfast/night/laundry: status 200, catalog-grounded
  product IDs and prices, no internal catalog/log wording.

## Remaining work

The next latency pass should add a bounded timeout/streaming response for
semantic turns, then compare answer quality against the current reviewed path.
The sub-second deterministic path must not be expanded to ambiguous symptoms,
medical claims, budgets or multi-turn corrections without a separate quality
check.
