# Catalog recommendation matrix — 2026-11-18

## Research basis

The test design follows four primary evaluation papers.

- [ShoppingBench](https://arxiv.org/abs/2508.04266) generates grounded shopping instructions from real product fields and checks terminal-state constraints such as product relevance and budget satisfaction.
- [Shopping Reasoning Bench](https://arxiv.org/abs/2606.12608) separates product recommendation, product inquiry, product comparison, shopping guidance and conversational navigation. It evaluates atomic, observable rubrics across turns instead of one general quality label.
- [ToolSandbox](https://arxiv.org/abs/2408.04682) and [$\tau$-bench](https://arxiv.org/abs/2406.12045) evaluate stateful trajectories with intermediate milestones, forbidden actions and final database state. This is the basis for the budget revision and topic-switch conversations.
- [RAGAS](https://arxiv.org/abs/2309.15217) separates retrieval relevance from answer faithfulness and answer relevance. The matrix therefore checks product IDs, catalog fields, constraints and response text independently.

## Question set

The reusable case file is [`vietnamese_catalog_recommendation_v1.json`](../../evaluation/cases/vietnamese_catalog_recommendation_v1.json). It contains 18 cases and 20 turns across nine suites.

| Suite | Cases | What it checks |
|---|---:|---|
| Occasion | 2 | Lẩu and late-night food use the right product types |
| Availability | 2 | Exact missing items fail closed and available milk stays in the milk family |
| Family browse | 1 | A broad “sữa” request spans verified milk categories |
| Budget | 5 | Strict upper bounds are respected across snacks, laundry, shampoo, tissue and pads |
| Package constraint | 1 | Oil recommendations preserve the 1-litre requirement |
| Promotion | 2 | Promotion results are category-filtered and backed by promotion rows |
| Ambiguity | 2 | “Khăn lau” does not become “lẩu”, and unknown battery size is not guessed |
| Safety | 1 | Unsupported acne-treatment claims are declined without SKU claims |
| Multi-turn | 2 | A budget revision and a topic switch update the active scope |

Run it from the repository root.

```powershell
.venv\Scripts\python.exe evaluation\run_catalog_recommendation_matrix.py `
  --output docs\reports\consultation_recommendation_matrix_20261118_live.json
```

## Results

The first live pass used the active snapshot and recorded 18 cases, 20 turns and a median response time of 83 ms. Four cases were initially flagged by the evaluator. Review showed that three flags came from overly literal rubric wording and one came from the evaluator's substring check, where `an lau` was found inside `khan lau`. The checker now uses word boundaries and catalog-level constraints. The recorded responses regrade to 18/18 cases and 100% of required rubrics in [`consultation_recommendation_matrix_20261118_final.json`](consultation_recommendation_matrix_20261118_final.json).

Fresh verification of the four initially flagged areas passed after the generic rubric and context fixes. The raw verification is in [`consultation_recommendation_matrix_20261118_verification.json`](consultation_recommendation_matrix_20261118_verification.json).

- 1-litre oil returned three catalog products with 1L/1 lít names.
- “Khăn lau” returned only towel products and no hotpot guidance.
- The AA/AAA battery request returned no product and did not guess a size. The provider fallback response was generic and took 5.76 seconds.
- The multi-turn hotpot-to-towel topic switch returned towel products on the second turn at 47 ms.

The live responses use the WinMart snapshot dated 15/09/2026. Promotion text is a snapshot observation and does not establish a currently active campaign or its terms.

## Code and regression changes

- Hotpot detection now requires an accented `lẩu` or a hotpot context such as `ăn lẩu`, `nồi lẩu` or `món lẩu`. A bare unaccented `lau` token is not enough.
- Budgeted catalog heads such as “nước giặt dưới 150 nghìn” can use the same generic fast path without filler words.
- The catalog consultation regression file now covers the context detector and budgeted catalog-head parsing. The targeted suite passed 21 tests.

The full repository suite passed 1,094 tests with `CATALOG_REPOSITORY=demo`, which is the fixture profile required by the existing demo-product tests. Running the full suite under the snapshot value from `.env` produces fixture mismatches for demo-only IDs such as `COKE_ZERO_330`; that run is not used as the regression result.

The matrix remains a coverage probe, not a statistical estimate. Repeat semantic cases across several trials when provider reliability or model consistency needs to be measured.
