# Catalog consultation quality — 2026-11-18

## Scope

This pass exercised the live Vietnamese text consultation route against the
Thanh Lam Mart snapshot. The focus was catalogue-wide intent handling rather than a
single product example: meal occasions, exact availability, broad category
browsing, strict budgets and named promotions.

## Changes

- Meal occasions now use a structured `scope=category` search backed by the
  imported taxonomy. Hotpot retrieval separates fresh meat, seafood and leafy
  vegetables; late-night retrieval uses individual porridge, instant-noodle and
  bao packs and removes wholesale cases.
- Occasion searches no longer treat normalized purpose words as product names.
  This prevents “lau” in “khăn lau” from becoming “lẩu” and prevents “cháo”
  from colliding with the unrelated “chạo” hierarchy after accent stripping.
- A broad one-word family request is expanded from verified category heads. For
  example, “các loại sữa” produces milk, yogurt and plant-milk category scopes
  instead of the first SKU whose flavour happens to contain “sữa”.
- Exact availability keeps the customer’s full phrase and fails closed when no
  matching row exists. It no longer turns “viên thả bồn cầu” into a generic
  cleaning claim.
- A strict recommendation budget now routes through the recommendation filter,
  so every returned snack card is below the requested limit. Named promotion
  searches filter the complete matching set before applying the result limit.
- Deterministic responses add short employee-style preparation guidance only
  when the product’s category and preparation scope support it.

## Live smoke results

Measured with `POST /agent/respond` on `127.0.0.1:8000`, fresh session, active
snapshot catalog:

| Query | Model path | Time |
|---|---|---:|
| `t muốn ăn lẩu thì ăn với cái gì` | deterministic catalog | 0.153 s |
| `đồ ăn đêm có cái gì ngon ko` | deterministic catalog | 0.145 s |
| `thế có các loại sữa nào` | deterministic catalog family | 0.324 s |
| `có loại snack nào giá dưới 20.000 đồng không?` | deterministic strict-budget | 0.372 s |
| `sữa tươi có khuyến mãi không?` | deterministic promotion lookup | 0.049 s |

The snack response returned 0 cards at or above 20,000đ. The promotion response
returned only rows whose names contain “sữa tươi”. The exact toilet-bowl query
returned a clear no-match response in about 0.03 s.

## Verification

- Full suite: **1085 passed**.
- Snapshot-specific catalog regressions: **83 passed**.
- New quality regressions cover availability parsing, family expansion, hotpot
  and late-night scopes, wholesale exclusion, strict snack budget, promotion
  filtering, cross-category preparation leakage and the “khăn lau” ambiguity.

## Remaining boundary

Complex preference, safety or medical-style turns still use the reviewed LLM
path. That is intentional: reducing those requests to lexical lookup would
make the response faster but could select the wrong product type or make an
unsupported claim.
