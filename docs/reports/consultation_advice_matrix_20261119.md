# Vietnamese consultation matrix — 2026-11-19

This matrix tested different departments and dialogue intents against the live
snapshot catalog. The check looked at product relevance, category, budget,
grounded IDs, refusal wording and latency.

| Case | Result | Live time |
|---|---|---:|
| Low-sugar drinks | Zero cola and unsweetened tea rows | 187 ms |
| Pet-safe cleaning request | Cleaning products plus label-check instruction | 72 ms |
| Late-night food | Porridge, instant noodles and bao | 170 ms |
| Lower-sugar children's snacks | Catalog rows explicitly labelled without sugar | 121 ms |
| OMO vs Ariel | One row per brand, no unsupported cleaning winner | 127 ms |
| Anti-dandruff shampoo | Selsun catalog row | 45 ms |
| Exact toilet-bowl tablet | Clear no-match response | 34 ms |
| Fresh-milk availability | Three fresh-milk rows | 83 ms |
| Snack under 20,000đ | All returned cards below the limit | 301 ms |
| Fresh-milk promotion | Promotion rows filtered by product phrase | 54 ms |

The fixes are general routing rules. Preference turns use catalog labels and
taxonomy leaves; pet-safety turns never assert safety from a generic cleaner
category; comparison turns retrieve each named brand; and exact availability
queries remain fail-closed. A named product with an unsupported health/use
claim still receives the existing verified-fact refusal.

The full backend suite now passes **1114 tests**. The focused catalog and API
regression set passes **439 tests**. Raw responses and timings are stored in
[consultation_advice_matrix_20261119_final.json](C:/Users/Chinhdz/.codex/worktrees/vietnamese-whisper-stt/Retail-AI--main/docs/reports/consultation_advice_matrix_20261119_final.json:1).

Remaining semantic turns such as open gift requests and broad value judgments
continue through the reviewed planner so the assistant can ask for the one
preference that changes the recommendation.
